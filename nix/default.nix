# nix/default.nix — Nix package for spotify-playlist-manager
#
# Builds the Python library + CLI as a single derivation.
# `buildPythonApplication` registers the console_scripts entry point
# from pyproject.toml and wraps the binary with the correct PYTHONPATH.
# librespot is added to PATH for the `stream` subcommand.

{ lib
, python3
, librespot
}:

python3.pkgs.buildPythonApplication {
  pname = "spotify-playlist-manager";
  version = "0.1.0";
  format = "pyproject";

  # Clean source: exclude git, cache, venv, and nix from the Nix store.
  src = lib.cleanSourceWith {
    filter = path: type:
      let
        base = builtins.baseNameOf path;
      in
      base != ".git"
      && base != "__pycache__"
      && base != ".pytest_cache"
      && base != ".venv"
      && base != "nix"
      && base != ".env"
      && base != ".spotify-cache"
      && base != "librespot.log";
    src = ./..;
  };

  nativeBuildInputs = with python3.pkgs; [
    setuptools
    wheel
  ];

  propagatedBuildInputs = with python3.pkgs; [
    spotipy
    python-dotenv
  ];

  # Tests require live Spotify credentials; skip in the Nix build.
  doCheck = false;

  # Add librespot to PATH so the `stream` subcommand can find it.
  postFixup = ''
    wrapProgram $out/bin/spotify-playlist-manager \
      --prefix PATH : ${lib.makeBinPath [ librespot ]}
  '';

  meta = with lib; {
    description = "A thin, friendly wrapper around the Spotify Web API for playlist manipulation.";
    homepage = "https://github.com/Cairnstew/spotify-playlist-manager";
    license = licenses.mit;
    maintainers = [ ];
    mainProgram = "spotify-playlist-manager";
  };
}
