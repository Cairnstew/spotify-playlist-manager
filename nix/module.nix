# nix/module.nix — NixOS module for spotify-playlist-manager
#
# Provides the package on PATH and optional credential configuration.
# Import from the flake:
#
#   inputs.spotify-playlist-manager.url = "github:Cairnstew/spotify-playlist-manager";
#
#   imports = [ inputs.spotify-playlist-manager.nixosModules.default ];
#   my.services.spotify-playlist-manager.enable = true;

{ config, lib, pkgs, ... }:

let
  cfg = config.services.spotify-playlist-manager;
in
{
  options.services.spotify-playlist-manager = {
    enable = lib.mkEnableOption "spotify-playlist-manager CLI";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.spotify-playlist-manager;
      defaultText = "pkgs.spotify-playlist-manager";
      description = "The spotify-playlist-manager package to use.";
    };

    # Credential environment variables.  These are written to a
    # mode-0600 EnvironmentFile and loaded by any systemd service
    # that uses this module.  For interactive use, source the file
    # or set the variables in your shell environment directly.
    credentials = {
      clientId = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "SPOTIFY_CLIENT_ID value.";
      };

      clientSecret = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "SPOTIFY_CLIENT_SECRET value.";
      };

      redirectUri = lib.mkOption {
        type = lib.types.str;
        default = "http://127.0.0.1:8877/callback";
        description = "SPOTIFY_REDIRECT_URI value.";
      };
    };

    # Path to an existing .env file (alternative to setting individual
    # credential options above).  When set, this takes precedence over
    # the individual credential options.
    envFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Path to a .env file with SPOTIFY_* variables.";
    };
  };

  config = lib.mkIf cfg.enable {
    # Make the package available system-wide.
    environment.systemPackages = [ cfg.package ];

    # Write a credentials file if individual options are provided.
    # The file is mode 0600 and owned by root, loadable by systemd.
    systemd.services.spotify-playlist-manager-env = lib.mkIf (cfg.envFile == null && cfg.credentials.clientId != "") {
      description = "Write spotify-playlist-manager credentials";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        mkdir -p /run/spotify-playlist-manager
        cat > /run/spotify-playlist-manager/.env <<'EOF'
        SPOTIFY_CLIENT_ID=${cfg.credentials.clientId}
        SPOTIFY_CLIENT_SECRET=${cfg.credentials.clientSecret}
        SPOTIFY_REDIRECT_URI=${cfg.credentials.redirectUri}
        EOF
        chmod 0600 /run/spotify-playlist-manager/.env
      '';
    };
  };
}
