{
  description = "Spotify Playlist Manager — package, module, and dev environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      # ── Packages ──────────────────────────────────────────────────────────
      # Exposes the Python app as `nix build .#spotify-playlist-manager`
      # and as the default package.
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          spotify-pkg = pkgs.callPackage ./nix/default.nix { };
        in
        {
          default = spotify-pkg;
          spotify-playlist-manager = spotify-pkg;
        }
      );

      # ── NixOS Module ──────────────────────────────────────────────────────
      # Import in your NixOS config:
      #
      #   inputs.spotify-playlist-manager.url = "github:Cairnstew/spotify-playlist-manager";
      #
      #   imports = [ inputs.spotify-playlist-manager.nixosModules.default ];
      #   services.spotify-playlist-manager.enable = true;
      #
      nixosModules.default = import ./nix/module.nix;

      # ── Dev Shell ─────────────────────────────────────────────────────────
      # `nix develop` drops you into a shell with Python, spotipy, pytest,
      # and librespot on PATH.
      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python3.withPackages (ps: with ps; [
            spotipy
            python-dotenv
            pytest
          ]);
        in
        {
          default = pkgs.mkShell {
            packages = [ python pkgs.librespot ];
            shellHook = ''
              echo "spotify-playlist-manager dev shell"
              python -c 'import spotipy; print("spotipy:", spotipy.__version__ if hasattr(spotipy, "__version__") else "installed")'
              command -v librespot >/dev/null && echo "librespot: $(command -v librespot)" || echo "librespot: MISSING"
            '';
          };
        }
      );
    };
}
