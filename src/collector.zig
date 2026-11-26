const std = @import("std");
const Header = @import("protocol.zig").Header;
const File = std.Io.File;

const max_open_scopes = 64;
const max_stream_name_len = "scope-".len + Header.max_scope_len * 3;

var child_pid: std.atomic.Value(std.posix.pid_t) = .init(0);

pub fn main(init: std.process.Init) void {
    const status = run(init) catch |err| {
        std.debug.print("fd-log-run: {s}\n", .{@errorName(err)});
        std.process.exit(1);
    };
    std.process.exit(status);
}

fn run(init: std.process.Init) !u8 {
    const io = init.io;
    const gpa = init.gpa;
    const args = try init.minimal.args.toSlice(init.arena.allocator());
    var index: usize = 1;
    var hook: ?[]const u8 = null;
    if (args.len > index and std.mem.eql(u8, args[index], "--on-stream")) {
        if (args.len <= index + 1) return usage();
        hook = args[index + 1];
        index += 2;
    }
    if (args.len < index + 3 or !std.mem.eql(u8, args[index + 1], "--")) return usage();
    const directory = args[index];
    var dir = try std.Io.Dir.cwd().createDirPathOpen(io, directory, .{});
    defer dir.close(io);

    const fds = try std.Io.Threaded.pipe2(.{ .CLOEXEC = true });
    var read: ?File = .{ .handle = fds[0], .flags = .{ .nonblocking = false } };
    var write: ?File = .{ .handle = fds[1], .flags = .{ .nonblocking = false } };
    defer if (read) |f| f.close(io);
    defer if (write) |f| f.close(io);
    // Keep the transport separate even when standard descriptors are closed.
    if (write.?.handle <= 2) {
        const fd = std.posix.system.fcntl(write.?.handle, std.posix.F.DUPFD_CLOEXEC, @as(usize, 3));
        if (std.posix.errno(fd) != .SUCCESS) return error.DuplicateFailed;
        write.?.close(io);
        write.?.handle = @intCast(fd);
    }
    var env = try init.environ_map.clone(gpa);
    defer env.deinit();
    const value = try std.fmt.allocPrint(gpa, "{d}", .{write.?.handle});
    defer gpa.free(value);
    try env.put("ZIG_LOG_FD", value);
    var child = try std.process.spawn(io, .{
        .argv = args[index + 2 ..],
        .environ_map = &env,
        .inherit_files = &.{write.?},
    });
    write.?.close(io);
    write = null;

    // Ignore Ctrl-C/SIGQUIT and forward SIGTERM so the app can log a graceful
    // shutdown while the collector drains remaining records until EOF.
    child_pid.store(child.id.?, .release);
    installSignalHandlers();

    // A failed collector closes its pipe so subsequent app logs fall back to
    // stderr. Still wait for the app; collection errors must not kill it.
    const result = collect(io, gpa, read.?, dir, directory, hook);
    read.?.close(io);
    read = null;
    const term = try child.wait(io);
    child_pid.store(0, .release);
    try result;
    return switch (term) {
        .exited => |status| status,
        .signal => |signal| @truncate(128 + @as(u32, @backingInt(signal))),
        else => 1,
    };
}

fn usage() u8 {
    std.debug.print("Usage: fd-log-run [--on-stream SCRIPT] DIRECTORY -- COMMAND [ARG ...]\n", .{});
    return 2;
}

fn installSignalHandlers() void {
    const ignore: std.posix.Sigaction = .{
        .handler = .{ .handler = std.posix.SIG.IGN },
        .mask = std.posix.sigemptyset(),
        .flags = 0,
    };
    std.posix.sigaction(.INT, &ignore, null);
    std.posix.sigaction(.QUIT, &ignore, null);
    const forward: std.posix.Sigaction = .{
        .handler = .{ .handler = forwardTerm },
        .mask = std.posix.sigemptyset(),
        .flags = std.posix.SA.RESTART,
    };
    std.posix.sigaction(.TERM, &forward, null);
}

fn forwardTerm(_: std.posix.SIG) callconv(.c) void {
    const pid = child_pid.load(.acquire);
    if (pid > 0) std.posix.kill(pid, .TERM) catch {};
}

const ScopeState = struct {
    file: ?File = null,
    used: u64 = 0,
};

fn collect(io: std.Io, gpa: std.mem.Allocator, pipe: File, dir: std.Io.Dir, directory: []const u8, hook: ?[]const u8) !void {
    var level_files: [4]?File = .{ null, null, null, null };
    defer for (level_files) |maybe_file| if (maybe_file) |f| f.close(io);

    var scopes: std.StringHashMap(ScopeState) = .init(gpa);
    defer {
        var it = scopes.iterator();
        while (it.next()) |entry| {
            if (entry.value_ptr.file) |f| f.close(io);
            gpa.free(entry.key_ptr.*);
        }
        scopes.deinit();
    }
    var open_scopes: usize = 0;
    var tick: u64 = 0;

    var read_buffer: [4096]u8 = undefined;
    var reader = pipe.readerStreaming(io, &read_buffer);
    while (try Header.read(&reader.interface)) |header| {
        var scope_buf: [Header.max_scope_len]u8 = undefined;
        const scope = scope_buf[0..header.scope_len];
        try reader.interface.readSliceAll(scope);

        var name_buf: [max_stream_name_len]u8 = undefined;
        var discovered = false;
        var file: File = undefined;
        var discovered_name: []const u8 = "";

        if (scope.len == 0) {
            const slot: usize = switch (header.level) {
                .err => 0,
                .warn => 1,
                .info => 2,
                .debug => 3,
            };
            if (level_files[slot]) |existing| {
                file = existing;
            } else {
                const name = @tagName(header.level);
                file = try openAppend(io, dir, name);
                level_files[slot] = file;
                discovered = true;
                discovered_name = name;
            }
        } else {
            const entry = try scopes.getOrPut(scope);
            if (!entry.found_existing) {
                errdefer _ = scopes.remove(scope);
                const name = formatStreamName(&name_buf, header.level, scope);
                if (open_scopes >= max_open_scopes) {
                    evictOldest(io, &scopes);
                    open_scopes -= 1;
                }
                const opened = try openAppend(io, dir, name);
                errdefer opened.close(io);
                entry.key_ptr.* = try gpa.dupe(u8, scope);
                entry.value_ptr.* = .{ .file = opened, .used = tick };
                open_scopes += 1;
                discovered = true;
                discovered_name = name;
                file = opened;
            } else if (entry.value_ptr.file) |existing| {
                entry.value_ptr.used = tick;
                file = existing;
            } else {
                const name = formatStreamName(&name_buf, header.level, scope);
                if (open_scopes >= max_open_scopes) {
                    evictOldest(io, &scopes);
                    open_scopes -= 1;
                }
                const opened = try openAppend(io, dir, name);
                entry.value_ptr.* = .{ .file = opened, .used = tick };
                open_scopes += 1;
                file = opened;
            }
            tick += 1;
        }

        var buffer: [4096]u8 = undefined;
        var writer = file.writerStreaming(io, &buffer);
        try reader.interface.streamExact64(&writer.interface, header.message_len);
        try writer.interface.flush();
        if (discovered) {
            if (hook) |program| {
                var viewer = try std.process.spawn(io, .{
                    .argv = &.{ program, directory, discovered_name },
                    .stdin = .ignore,
                    .stdout = .ignore,
                });
                if (!(try viewer.wait(io)).success()) return error.ViewerFailed;
            }
        }
    }
}

fn openAppend(io: std.Io, dir: std.Io.Dir, name: []const u8) !File {
    var path_buf: [max_stream_name_len + ".log".len]u8 = undefined;
    const path = std.fmt.bufPrint(&path_buf, "{s}.log", .{name}) catch unreachable;
    const file = try dir.createFile(io, path, .{ .truncate = false });
    errdefer file.close(io);
    const flags = std.posix.system.fcntl(file.handle, std.posix.F.GETFL, @as(usize, 0));
    if (std.posix.errno(flags) != .SUCCESS) return error.AppendFailed;
    var options: std.posix.O = @bitCast(@as(u32, @intCast(flags)));
    options.APPEND = true;
    if (std.posix.errno(std.posix.system.fcntl(file.handle, std.posix.F.SETFL, @as(u32, @bitCast(options)))) != .SUCCESS)
        return error.AppendFailed;
    return file;
}

fn evictOldest(io: std.Io, scopes: *std.StringHashMap(ScopeState)) void {
    var victim: ?*ScopeState = null;
    var it = scopes.valueIterator();
    while (it.next()) |state| {
        if (state.file == null) continue;
        if (victim == null or state.used < victim.?.used) victim = state;
    }
    if (victim) |state| {
        state.file.?.close(io);
        state.file = null;
    }
}

fn formatStreamName(buf: *[max_stream_name_len]u8, level: std.log.Level, scope: []const u8) []const u8 {
    if (scope.len == 0) return @tagName(level);
    var writer: std.Io.Writer = .fixed(buf);
    writer.writeAll("scope-") catch unreachable;
    for (scope) |byte| {
        if (std.ascii.isAlphanumeric(byte) or byte == '-' or byte == '_') {
            writer.writeByte(byte) catch unreachable;
        } else {
            writer.print("%{X:0>2}", .{byte}) catch unreachable;
        }
    }
    return writer.buffered();
}

test "formatStreamName escapes unsafe bytes and separates default scope" {
    var buf: [max_stream_name_len]u8 = undefined;
    try std.testing.expectEqualStrings("err", formatStreamName(&buf, .err, ""));
    try std.testing.expectEqualStrings("scope-err", formatStreamName(&buf, .warn, "err"));
    try std.testing.expectEqualStrings("scope-net_work-1", formatStreamName(&buf, .info, "net_work-1"));
    try std.testing.expectEqualStrings(
        "scope-%2E%2E%2F%E6%97%A5%E6%9C%AC%E8%AA%9E",
        formatStreamName(&buf, .debug, "../日本語"),
    );
}
