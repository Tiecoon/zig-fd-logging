const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const module = b.addModule("fd_log", .{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
    });
    const example = b.addExecutable(.{
        .name = "fd-log-example",
        .root_module = b.createModule(.{
            .root_source_file = b.path("examples/main.zig"),
            .target = target,
            .optimize = optimize,
            .imports = &.{.{ .name = "fd_log", .module = module }},
        }),
    });
    b.installArtifact(example);
    const test_command = b.addSystemCommand(&.{ "python3", "tests/integration.py" });
    test_command.addArtifactArg(example);
    b.step("test", "Test routing, filtering, shell launchers, and error handling").dependOn(&test_command.step);
}
