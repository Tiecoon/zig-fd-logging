//! An optional std.log transport. Without a collector, use std.log unchanged.
const std = @import("std");
const Header = @import("protocol.zig").Header;

var descriptor: std.atomic.Value(std.posix.fd_t) = .init(-1);
var mutex: std.Io.Mutex = .init;

/// Call once before spawning threads. Only the collector sets ZIG_LOG_FD.
/// Missing, stale, or invalid configuration leaves ordinary std.log enabled.
/// The descriptor is borrowed and must not be closed or repurposed by the caller.
pub fn configureFromEnvironment(env: *const std.process.Environ.Map) void {
    descriptor.store(-1, .release);
    const value = env.get("ZIG_LOG_FD") orelse return;
    const fd = std.fmt.parseInt(std.posix.fd_t, value, 10) catch return;
    if (fd <= 2) return;
    const flags = std.posix.system.fcntl(fd, std.posix.F.GETFL, @as(usize, 0));
    if (std.posix.errno(flags) != .SUCCESS) return;
    const options: std.posix.O = @bitCast(@as(u32, @intCast(flags)));
    if (options.ACCMODE == .RDONLY or options.NONBLOCK) return;
    const file: std.Io.File = .{ .handle = fd, .flags = .{ .nonblocking = false } };
    const stat = file.stat(std.Options.debug_io) catch return;
    if (stat.kind != .named_pipe) return;
    // Unrelated exec'd children must not keep the collector alive. The marker
    // can remain in their environment: a closed descriptor simply falls back.
    if (std.posix.errno(std.posix.system.fcntl(fd, std.posix.F.SETFD, @as(u32, std.posix.FD_CLOEXEC))) != .SUCCESS) return;
    descriptor.store(fd, .release);
}

/// Install as std_options.logFn. Levels, scopes, filtering, and formatting stay
/// owned by std.log. Ordinary logging does not allocate or acquire our mutex.
pub fn logFn(
    comptime level: std.log.Level,
    comptime scope: @EnumLiteral(),
    comptime format: []const u8,
    args: anytype,
) void {
    if (descriptor.load(.acquire) < 0)
        return std.log.defaultLog(level, scope, format, args);

    // Format outside the mutex so custom formatters can't deadlock or serialize
    // across threads, and assemble one contiguous frame to avoid extra copies.
    const scope_name = if (scope == .default) "" else @tagName(scope);
    const prefix_len = Header.size + scope_name.len;
    var storage: [1024]u8 = undefined;
    var fallback = std.heap.BufferFirstAllocator.init(&storage, std.heap.page_allocator);
    var frame = std.Io.Writer.Allocating.initCapacity(fallback.allocator(), storage.len) catch
        return std.log.defaultLog(level, scope, format, args);
    defer frame.deinit();
    frame.writer.splatByteAll(0, Header.size) catch
        return std.log.defaultLog(level, scope, format, args);
    frame.writer.writeAll(scope_name) catch
        return std.log.defaultLog(level, scope, format, args);
    std.log.defaultLogFileTerminal(level, scope, format, args, .{
        .writer = &frame.writer,
        .mode = .no_color,
    }) catch return std.log.defaultLog(level, scope, format, args);

    const bytes = frame.written();
    const header: Header = .{
        .level = level,
        .scope_len = scope_name.len,
        .message_len = bytes.len - prefix_len,
    };
    bytes[0..Header.size].* = header.encode();

    const sent = blk: {
        const io = std.Options.debug_io;
        const previous = io.swapCancelProtection(.blocked);
        defer _ = io.swapCancelProtection(previous);
        mutex.lockUncancelable(io);
        defer mutex.unlock(io);
        const fd = descriptor.load(.acquire);
        if (fd < 0) break :blk false;
        const file: std.Io.File = .{ .handle = fd, .flags = .{ .nonblocking = false } };
        file.writeStreamingAll(io, bytes) catch {
            descriptor.store(-1, .release);
            break :blk false;
        };
        break :blk true;
    };
    if (!sent) std.log.defaultLog(level, scope, format, args);
}
