const std = @import("std");

fn addKknd(
    b: *std.Build,
    target: std.Build.ResolvedTarget,
    optimize: std.builtin.OptimizeMode,
    c_flags: []const []const u8,
    name: []const u8,
) *std.Build.Step.Compile {
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

    kknd_mod.linkSystemLibrary("version", .{});
    kknd_mod.linkSystemLibrary("gdi32", .{});
    kknd_mod.linkSystemLibrary("winmm", .{});

    if (target.result.cpu.arch != .x86_64) {
        // Windows/DirectX import libs, x86 only. zig's bundled mingw ships .def
        // files for these and synthesizes the import lib at link time, so
        // nothing needs vendoring. dplayx (DirectPlay) has no x64 import lib in
        // zig's mingw at all; the DirectPlay entry points kknd.c imports are
        // stubbed out there (LAN/DirectPlay multiplayer disabled on x64).
        for ([_][]const u8{ "ddraw", "dsound", "dplayx" }) |lib| {
            kknd_mod.linkSystemLibrary(lib, .{});
        }
    } else {
        // x64 drops DirectDraw/DirectSound for wgpu-native (WebGPU), vendored
        // as prebuilt gnu-ABI binaries matching zig's bundled mingw.
        kknd_mod.addSystemIncludePath(b.path("vendor/wgpu-native/include"));
        kknd_mod.addLibraryPath(b.path("vendor/wgpu-native/lib"));
        kknd_mod.linkSystemLibrary("wgpu_native", .{});
    }

    return b.addExecutable(.{
        .name = name,
        .root_module = kknd_mod,
    });
}

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{ .default_target = .{
        .cpu_arch = .x86,
        .os_tag = .windows,
    } });
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
    kknd.subsystem = .console;
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
    kknd_x64.subsystem = if (optimize == .Debug)
        .console
    else
        .windows;
    b.installArtifact(kknd_x64);

    // wgpu_native.dll must sit next to kknd-x64.exe; it's linked dynamically
    // via the vendored import lib (see addKknd).
    const install_wgpu_dll = b.addInstallFileWithDir(
        b.path("vendor/wgpu-native/lib/wgpu_native.dll"),
        .bin,
        "wgpu_native.dll",
    );
    b.getInstallStep().dependOn(&install_wgpu_dll.step);

    const run_x64 = b.addRunArtifact(kknd_x64);
    run_x64.step.dependOn(b.getInstallStep());
    if (b.args) |args| {
        run_x64.addArgs(args);
    }
    const run_x64_step = b.step("run-x64", "Run kknd (x86_64)");
    run_x64_step.dependOn(&run_x64.step);
}
