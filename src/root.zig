//! A std.log sink for explicitly inherited POSIX file descriptors.
//! Install logFn in the executable's std_options and configure before spawning
//! threads. Descriptors are borrowed: do not close or repurpose them while logging.
const std = @import("std");

pub const Routes = struct {
    err: ?std.posix.fd_t = null,
    warn: ?std.posix.fd_t = null,
    info: ?std.posix.fd_t = null,
    debug: ?std.posix.fd_t = null,

    pub const inherited: Routes = .{ .err = 3, .warn = 4, .info = 5, .debug = 6 };
};

var routes: Routes = .{};
var mutex: std.Io.Mutex = .init;

/// Configure before concurrent logging. Null routes use std.log.defaultLog.
/// Requires writable, blocking POSIX descriptors. No ownership is transferred.
/// Invalid routes leave the previous configuration unchanged.
pub fn configure(new_routes: Routes) error{InvalidDescriptor}!void {
    inline for (@typeInfo(Routes).@"struct".field_names) |name| {
        if (@field(new_routes, name)) |fd| {
            if (fd < 0) return error.InvalidDescriptor;
            const flags = std.posix.system.fcntl(fd, std.posix.F.GETFL, @as(usize, 0));
            if (std.posix.errno(flags) != .SUCCESS) return error.InvalidDescriptor;
            const options: std.posix.O = @bitCast(@as(u32, @intCast(flags)));
            if (options.ACCMODE == .RDONLY or options.NONBLOCK) return error.InvalidDescriptor;
        }
    }
    routes = new_routes;
}

/// The provided launcher sets ZIG_LOG_FDS=1 after opening descriptors 3 through 6.
/// Without this opt-in, preserve ordinary stderr logging, even if extra FDs exist.
pub fn configureFromEnvironment(env: *const std.process.Environ.Map) error{InvalidDescriptor}!void {
    const enabled = if (env.get("ZIG_LOG_FDS")) |value| std.mem.eql(u8, value, "1") else false;
    try configure(if (enabled) .inherited else .{});
}

/// Use as std_options.logFn; levels, scopes and filtering remain owned by std.log.
pub fn logFn(
    comptime level: std.log.Level,
    comptime scope: @EnumLiteral(),
    comptime format: []const u8,
    args: anytype,
) void {
    const fd = @field(routes, @tagName(level)) orelse
        return std.log.defaultLog(level, scope, format, args);

    const io = std.Options.debug_io;
    const previous = io.swapCancelProtection(.blocked);
    defer _ = io.swapCancelProtection(previous);
    mutex.lockUncancelable(io);
    defer mutex.unlock(io);

    // Streaming preserves the inherited file offset; positional writers would
    // overwrite earlier messages when a fresh writer is created for each call.
    const file: std.Io.File = .{ .handle = fd, .flags = .{ .nonblocking = false } };
    var buffer: [1024]u8 = undefined;
    var writer = file.writerStreaming(io, &buffer);
    // File sinks deliberately contain no terminal escapes. stderr keeps std's
    // own terminal policy through defaultLog above.
    std.log.defaultLogFileTerminal(level, scope, format, args, .{
        .writer = &writer.interface,
        .mode = .no_color,
    }) catch return;
    writer.interface.flush() catch {};
}
