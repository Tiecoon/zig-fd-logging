{
  description = "std.log routing to inherited per-level POSIX descriptors";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor = system: import nixpkgs { inherit system; };
    in {
      homeManagerModules.default = { config, lib, pkgs, ... }:
        let
          helpers = self.packages.${pkgs.stdenv.hostPlatform.system}.helpers;
          source = extension: ''source "${helpers}/share/fd-log/config.${extension}"'';
        in {
          home.packages = [ helpers ];
          programs.bash.initExtra = lib.mkIf config.programs.bash.enable (source "sh");
          programs.zsh.initContent = lib.mkIf config.programs.zsh.enable (source "sh");
          programs.fish.interactiveShellInit = lib.mkIf config.programs.fish.enable (source "fish");
          programs.nushell.extraConfig = lib.mkIf config.programs.nushell.enable (source "nu");
        };

      packages = forAllSystems (system:
        let pkgs = pkgsFor system;
        in {
          default = pkgs.symlinkJoin {
            name = "zig-fd-logging";
            paths = with self.packages.${system}; [ example helpers ];
          };
          helpers = pkgs.stdenvNoCC.mkDerivation {
            pname = "fd-log-helpers";
            version = "0.1.0";
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [ ./scripts ./examples/shell ];
            };
            nativeBuildInputs = [ pkgs.makeWrapper ];
            dontBuild = true;
            installPhase = ''
              install -Dm755 scripts/fd-log-run "$out/bin/fd-log-run"
              install -Dm755 scripts/fd-log-panes "$out/bin/fd-log-panes"
              patchShebangs "$out/bin"
              wrapProgram "$out/bin/fd-log-run" --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.coreutils ]}
              wrapProgram "$out/bin/fd-log-panes" --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.coreutils pkgs.bash pkgs.tmux ]}
              mkdir -p "$out/share/fd-log"
              cp examples/shell/config.* "$out/share/fd-log/"
            '';
          };
          example = pkgs.stdenvNoCC.mkDerivation {
            pname = "zig-fd-logging-example";
            version = "0.1.0";
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [
                ./build.zig ./build.zig.zon ./src ./examples ./scripts ./tests
                ./README.md ./flake.nix ./flake.lock
              ];
            };
            nativeBuildInputs = with pkgs; [ zig python3 bash zsh fish nushell ];
            postPatch = "patchShebangs scripts";
            buildPhase = ''
              export ZIG_GLOBAL_CACHE_DIR="$TMPDIR/zig-cache"
              zig build --prefix "$out"
            '';
            doCheck = true;
            checkPhase = "zig build test";
            installPhase = ''
              install -Dm644 README.md "$out/share/doc/zig-fd-logging/README.md"
            '';
          };
        });
      checks = forAllSystems (system: {
        integration = self.packages.${system}.example;
        helpers = self.packages.${system}.helpers;
      });
      devShells = forAllSystems (system:
        let pkgs = pkgsFor system;
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [ zig python3 bash zsh fish nushell tmux shellcheck ];
          };
        });
    };
}
