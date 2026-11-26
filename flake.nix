{
  description = "Optional std.log collection over one inherited pipe";
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
        let
          pkgs = pkgsFor system;
          compiled = pkgs.stdenvNoCC.mkDerivation {
            pname = "zig-fd-logging";
            version = "0.1.0";
            outputs = [ "out" "example" ];
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [
                ./build.zig ./build.zig.zon ./src ./python ./examples ./scripts ./tests
                ./README.md ./flake.nix ./flake.lock
              ];
            };
            nativeBuildInputs = with pkgs; [ zig python3 bash zsh fish nushell ];
            postPatch = "patchShebangs scripts";
            buildPhase = ''
              export ZIG_GLOBAL_CACHE_DIR="$TMPDIR/zig-cache"
              zig build -Doptimize=ReleaseSafe --prefix "$out"
            '';
            doCheck = true;
            checkPhase = "zig build test";
            installPhase = ''
              mkdir -p "$example/bin"
              mv "$out/bin/fd-log-example" "$example/bin/"
              install -Dm644 README.md "$out/share/doc/zig-fd-logging/README.md"
            '';
          };
        in {
          collector = compiled;
          example = compiled.example;
          python = pkgs.python3Packages.buildPythonPackage {
            pname = "zig-fd-logging";
            version = "0.1.0";
            src = ./python;
            pyproject = true;
            build-system = [ pkgs.python3Packages.setuptools ];
            pythonImportsCheck = [ "zig_fd_logging" ];
          };
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
              install -Dm755 scripts/fd-log-panes "$out/bin/fd-log-panes"
              install -Dm755 scripts/fd-log-view "$out/bin/fd-log-view"
              ln -s ${compiled}/bin/fd-log-run "$out/bin/fd-log-run"
              patchShebangs "$out/bin"
              for helper in fd-log-panes fd-log-view; do
                wrapProgram "$out/bin/$helper" --suffix PATH : ${pkgs.lib.makeBinPath [ compiled pkgs.coreutils pkgs.bash pkgs.tmux ]}
              done
              mkdir -p "$out/share/fd-log"
              cp examples/shell/config.* "$out/share/fd-log/"
            '';
          };
        });
      checks = forAllSystems (system:
        let pkgs = pkgsFor system;
        in {
          integration = self.packages.${system}.collector;
          helpers = self.packages.${system}.helpers;
          python = self.packages.${system}.python;
          shellcheck = pkgs.runCommand "fd-log-shellcheck" {
            nativeBuildInputs = [ pkgs.shellcheck ];
          } ''
            shellcheck ${./scripts/fd-log-panes} ${./scripts/fd-log-view}
            shellcheck -s bash ${./examples/shell/config.sh}
            touch "$out"
          '';
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
