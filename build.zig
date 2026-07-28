const std = @import("std");

fn addKknd(
    b: *std.Build,
    target: std.Build.ResolvedTarget,
    optimize: std.builtin.OptimizeMode,
    c_flags: []const []const u8,
    name: []const u8,
) *std.Build.Step.Compile {
    // --- raylib (from zig package manager) ---
    const raylib_dep = b.dependency("raylib", .{
        .target = target,
        .optimize = optimize,
    });
    const raylib_lib = raylib_dep.artifact("raylib");

    // --- kknd executable ---
    const kknd_mod = b.createModule(.{
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });

    kknd_mod.addCSourceFiles(.{
        .files = &.{"src/kknd/kknd.c"},
        .flags = c_flags,
    });

    kknd_mod.addIncludePath(b.path("src/kknd"));
    kknd_mod.addSystemIncludePath(b.path("vendor"));
    kknd_mod.addIncludePath(raylib_dep.path("src"));
    kknd_mod.linkLibrary(raylib_lib);

    // Windows/DirectX import libs. zig's bundled mingw ships .def files for these
    // and synthesizes the import lib at link time, so nothing needs vendoring.
    for ([_][]const u8{ "ddraw", "dsound", "version" }) |lib| {
        kknd_mod.linkSystemLibrary(lib, .{});
    }
    // dplayx (DirectPlay) is a deprecated API with no x64 import lib in zig's
    // mingw. Only link it for x86; on x64 the DirectPlay entry points we import
    // are stubbed in kknd.c (LAN/DirectPlay multiplayer disabled there).
    if (target.result.cpu.arch != .x86_64) {
        kknd_mod.linkSystemLibrary("dplayx", .{});
    }

    return b.addExecutable(.{
        .name = name,
        .root_module = kknd_mod,
    });
}

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{
        .default_target = .{
            .cpu_arch = .x86,
            .os_tag = .windows,
        }
    });
    const optimize = b.standardOptimizeOption(.{});

    // Windows target defaults to CodeView-only debug info (-> kknd.pdb), which gdb
    // can't read. In Debug builds also embed DWARF in the PE so gdb works too;
    // -gcodeview keeps the PDB path intact for the MSVC-style debugger.
    const base_c_flags = [_][]const u8{ "-std=c2x", "-Wall", "-Wextra" };
    const debug_c_flags = [_][]const u8{ "-gcodeview", "-gdwarf-4" };
    const c_flags: []const []const u8 = if (optimize == .Debug)
        &(base_c_flags ++ debug_c_flags)
    else
        &base_c_flags;

    // --- 32-bit x86 (default, honours -Dtarget=...) ---
    const kknd = addKknd(b, target, optimize, c_flags, "kknd");
    b.installArtifact(kknd);

    const run_cmd = b.addRunArtifact(kknd);
    run_cmd.step.dependOn(b.getInstallStep());
    if (b.args) |args| {
        run_cmd.addArgs(args);
    }
    const run_step = b.step("run", "Run kknd (x86)");
    run_step.dependOn(&run_cmd.step);

    // --- 64-bit x86_64 (new; exercises the pointer-widening level loader) ---
    const target_x64 = b.resolveTargetQuery(.{
        .cpu_arch = .x86_64,
        .os_tag = .windows,
    });
    const kknd_x64 = addKknd(b, target_x64, optimize, c_flags, "kknd-x64");
    b.installArtifact(kknd_x64);

    const run_x64 = b.addRunArtifact(kknd_x64);
    run_x64.step.dependOn(b.getInstallStep());
    if (b.args) |args| {
        run_x64.addArgs(args);
    }
    const run_x64_step = b.step("run-x64", "Run kknd (x86_64)");
    run_x64_step.dependOn(&run_x64.step);
}
