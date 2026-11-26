const std = @import("std");
const fd_log = @import("fd_log");

pub const std_options: std.Options = .{
    .logFn = fd_log.logFn,
    .log_level = .debug,
    .log_scope_levels = &.{.{ .scope = .quiet, .level = .warn }},
};

pub fn main(init: std.process.Init) !void {
    fd_log.configureFromEnvironment(init.environ_map);
    const args = try init.minimal.args.toSlice(init.arena.allocator());
    try std.Io.File.stdout().writeStreamingAll(init.io, "stdout stays stdout\n");
    try std.Io.File.stderr().writeStreamingAll(init.io, "stderr stays stderr\n");
    std.log.err("error {d}", .{17});
    std.log.warn("warning {s}", .{"careful"});
    std.log.info("info {d}", .{23});
    std.log.debug("debug {x}", .{@as(u16, 0xabcd)});
    std.log.scoped(.network).info("connected {d}", .{42});
    std.log.scoped(.quiet).info("must not appear", .{});
    std.log.scoped(.quiet).warn("visible", .{});
    if (args.len > 1 and std.mem.eql(u8, args[1], "--scopes")) {
        std.log.scoped(.network).err("disconnected", .{});
        std.log.scoped(.database).info("transaction\ncommitted", .{});
        std.log.scoped(.err).warn("a scope, not a level", .{});
        std.log.scoped(.@"../name with 'quotes' $;\n").debug("safe filename", .{});
        std.log.scoped(.@"日本語").info("unicode scope", .{});
        std.log.scoped(.worker).info("working", .{});
    }
    if (args.len > 1 and std.mem.eql(u8, args[1], "--concurrent")) {
        var group: std.Io.Group = .init;
        defer group.cancel(init.io);
        for (0..4) |worker| try group.concurrent(init.io, logWorker, .{worker});
        try group.await(init.io);
    }
    if (args.len > 1 and std.mem.eql(u8, args[1], "--long")) {
        const long_message: [8192]u8 = @splat('x');
        std.log.info("{s}", .{&long_message});
        std.log.info("after long message", .{});
    }
}

fn logWorker(worker: usize) void {
    const payload: [2048]u8 = @splat('y');
    for (0..50) |sequence| {
        std.log.info("worker={d} sequence={d} {s}", .{ worker, sequence, &payload });
    }
}
