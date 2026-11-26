const std = @import("std");

// Internal wire format: severity byte (0=err, 1=warn, 2=info, 3=debug),
// scope length (u32 LE), message length (u64 LE), then the scope bytes and
// upstream-formatted message bytes. An empty scope denotes the default/root
// logger, not a reserved scope name.
pub const Header = struct {
    pub const size = 13;
    pub const max_scope_len = 255;

    level: std.log.Level,
    scope_len: u32,
    message_len: u64,

    pub fn encode(header: Header) [size]u8 {
        var bytes: [size]u8 = undefined;
        bytes[0] = switch (header.level) {
            .err => 0,
            .warn => 1,
            .info => 2,
            .debug => 3,
        };
        std.mem.writeInt(u32, bytes[1..5], header.scope_len, .little);
        std.mem.writeInt(u64, bytes[5..13], header.message_len, .little);
        return bytes;
    }

    pub fn read(reader: *std.Io.Reader) !?Header {
        var bytes: [size]u8 = undefined;
        const len = try reader.readSliceShort(&bytes);
        if (len == 0) return null;
        if (len != bytes.len) return error.TruncatedRecord;
        const level: std.log.Level = switch (bytes[0]) {
            0 => .err,
            1 => .warn,
            2 => .info,
            3 => .debug,
            else => return error.InvalidLevel,
        };
        const scope_len = std.mem.readInt(u32, bytes[1..5], .little);
        if (scope_len > max_scope_len) return error.ScopeTooLong;
        return .{
            .level = level,
            .scope_len = scope_len,
            .message_len = std.mem.readInt(u64, bytes[5..13], .little),
        };
    }
};

test "header encode and read" {
    for ([_]std.log.Level{ .err, .warn, .info, .debug }) |level| {
        const expected: Header = .{ .level = level, .scope_len = 7, .message_len = 42 };
        const encoded = expected.encode();
        var reader: std.Io.Reader = .fixed(&encoded);
        try std.testing.expectEqual(expected, (try Header.read(&reader)).?);
        try std.testing.expectEqual(@as(?Header, null), try Header.read(&reader));
    }
}

test "header read rejects invalid, truncated, and oversized headers" {
    var truncated: std.Io.Reader = .fixed(&.{ 2, 0 });
    try std.testing.expectError(error.TruncatedRecord, Header.read(&truncated));

    var invalid_bytes = (Header{ .level = .info, .scope_len = 0, .message_len = 0 }).encode();
    invalid_bytes[0] = 9;
    var invalid: std.Io.Reader = .fixed(&invalid_bytes);
    try std.testing.expectError(error.InvalidLevel, Header.read(&invalid));

    const oversized_bytes = (Header{ .level = .info, .scope_len = Header.max_scope_len + 1, .message_len = 0 }).encode();
    var oversized: std.Io.Reader = .fixed(&oversized_bytes);
    try std.testing.expectError(error.ScopeTooLong, Header.read(&oversized));
}
