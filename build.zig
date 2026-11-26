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
    const collector_module = b.createModule(.{
        .root_source_file = b.path("src/collector.zig"),
        .target = target,
        .optimize = optimize,
    });
    const collector = b.addExecutable(.{
        .name = "fd-log-run",
        .root_module = collector_module,
    });
    b.installArtifact(example);
    b.installArtifact(collector);
    const unit_tests = b.addRunArtifact(b.addTest(.{ .root_module = collector_module }));
    const protocol_tests = b.addRunArtifact(b.addTest(.{
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/protocol.zig"),
            .target = target,
            .optimize = optimize,
        }),
    }));
    const test_command = b.addSystemCommand(&.{ "python3", "tests/integration.py" });
    test_command.addArtifactArg(example);
    test_command.addArtifactArg(collector);
    const python_tests = b.addSystemCommand(&.{ "python3", "tests/python_client.py" });
    python_tests.addArtifactArg(collector);
    const tests = b.step("test", "Test Zig/Python logging, collection, and shell integration");
    tests.dependOn(&unit_tests.step);
    tests.dependOn(&protocol_tests.step);
    tests.dependOn(&test_command.step);
    tests.dependOn(&python_tests.step);
}
