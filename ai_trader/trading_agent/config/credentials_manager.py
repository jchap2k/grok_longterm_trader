#!/usr/bin/env python3
"""
Credentials Manager

Securely loads API keys from files in the config directory.
Falls back to environment variables if files don't exist.
"""

import os
from pathlib import Path
from typing import Optional, Dict
import json


class CredentialsManager:
    """
    Manages API credentials from files or environment variables.

    Directory structure:
        config/
            anthropic_key.txt       - Claude API key
            schwab_app_key.txt      - Schwab app key
            schwab_app_secret.txt   - Schwab app secret
            schwab_refresh_token.txt - Schwab refresh token
            alpaca_api_key.txt      - Alpaca API key
            alpaca_secret_key.txt   - Alpaca secret key
            broker_config.json      - Broker selection and preferences
    """

    def __init__(self, config_dir: Path = None):
        """
        Initialize credentials manager.

        Args:
            config_dir: Path to config directory (defaults to ./config)
        """
        if config_dir is None:
            # Default to config directory in same folder as this script
            self.config_dir = Path(__file__).parent
        else:
            self.config_dir = Path(config_dir)

        # Create config directory if it doesn't exist
        self.config_dir.mkdir(exist_ok=True, parents=True)

        # Create .gitignore to protect credentials
        gitignore_path = self.config_dir / ".gitignore"
        if not gitignore_path.exists():
            with open(gitignore_path, 'w') as f:
                f.write("# Protect API keys and credentials\n")
                f.write("*_key.txt\n")
                f.write("*_secret.txt\n")
                f.write("*_token.txt\n")
                f.write("broker_config.json\n")
                f.write("!.gitignore\n")
                f.write("!README.md\n")

    def _read_file(self, filename: str) -> Optional[str]:
        """
        Read a credential from a file.

        Args:
            filename: Name of the file to read

        Returns:
            Contents of the file (stripped), or None if file doesn't exist
        """
        filepath = self.config_dir / filename

        if not filepath.exists():
            return None

        try:
            with open(filepath, 'r') as f:
                content = f.read().strip()
                return content if content else None
        except Exception as e:
            print(f"Warning: Error reading {filename}: {e}")
            return None

    def _write_file(self, filename: str, content: str):
        """
        Write a credential to a file.

        Args:
            filename: Name of the file to write
            content: Content to write
        """
        filepath = self.config_dir / filename

        try:
            with open(filepath, 'w') as f:
                f.write(content.strip())

            # Set restrictive permissions on Unix-like systems
            if hasattr(os, 'chmod'):
                os.chmod(filepath, 0o600)  # rw------- (owner only)

        except Exception as e:
            print(f"Error writing {filename}: {e}")

    def get_anthropic_key(self) -> Optional[str]:
        """Get Anthropic API key from file or environment."""
        # Try file first
        key = self._read_file("anthropic_key.txt")
        if key:
            return key

        # Fall back to environment variable
        return os.getenv("ANTHROPIC_API_KEY")

    def set_anthropic_key(self, key: str):
        """Save Anthropic API key to file."""
        self._write_file("anthropic_key.txt", key)

    def get_schwab_credentials(self) -> Dict[str, Optional[str]]:
        """Get all Schwab credentials from files or environment."""
        return {
            'app_key': self._read_file("schwab_app_key.txt") or os.getenv("SCHWAB_APP_KEY"),
            'app_secret': self._read_file("schwab_app_secret.txt") or os.getenv("SCHWAB_APP_SECRET"),
            'refresh_token': self._read_file("schwab_refresh_token.txt") or os.getenv("SCHWAB_REFRESH_TOKEN"),
        }

    def set_schwab_credentials(self, app_key: str = None, app_secret: str = None, refresh_token: str = None):
        """Save Schwab credentials to files."""
        if app_key:
            self._write_file("schwab_app_key.txt", app_key)
        if app_secret:
            self._write_file("schwab_app_secret.txt", app_secret)
        if refresh_token:
            self._write_file("schwab_refresh_token.txt", refresh_token)

    def get_alpaca_credentials(self) -> Dict[str, Optional[str]]:
        """Get Alpaca credentials from files or environment."""
        return {
            'api_key': self._read_file("alpaca_api_key.txt") or os.getenv("ALPACA_API_KEY"),
            'secret_key': self._read_file("alpaca_secret_key.txt") or os.getenv("ALPACA_SECRET_KEY"),
        }

    def set_alpaca_credentials(self, api_key: str = None, secret_key: str = None):
        """Save Alpaca credentials to files."""
        if api_key:
            self._write_file("alpaca_api_key.txt", api_key)
        if secret_key:
            self._write_file("alpaca_secret_key.txt", secret_key)

    def get_xai_key(self) -> Optional[str]:
        """Get xAI API key from file or environment."""
        # Try file first
        key = self._read_file("xai_api_key.txt")
        if key:
            return key

        # Fall back to environment variable
        return os.getenv("XAI_API_KEY")

    def set_xai_key(self, key: str):
        """Save xAI API key to file."""
        self._write_file("xai_api_key.txt", key)

    def get_broker_config(self) -> Dict:
        """
        Get broker configuration from file.

        Returns:
            Dictionary with broker settings, or defaults
        """
        config_file = self.config_dir / "broker_config.json"

        # Defaults
        defaults = {
            'broker': 'alpaca',
            'paper_trading': True,
            'starting_cash': 35000.0,
        }

        if not config_file.exists():
            return defaults

        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                # Merge with defaults (in case file is missing keys)
                return {**defaults, **config}
        except Exception as e:
            print(f"Warning: Error reading broker_config.json: {e}")
            return defaults

    def set_broker_config(self, broker: str = None, paper_trading: bool = None, starting_cash: float = None):
        """Save broker configuration to file."""
        config_file = self.config_dir / "broker_config.json"

        # Load existing config or start with defaults
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
            except:
                config = {}
        else:
            config = {}

        # Update with new values
        if broker is not None:
            config['broker'] = broker
        if paper_trading is not None:
            config['paper_trading'] = paper_trading
        if starting_cash is not None:
            config['starting_cash'] = starting_cash

        # Write back
        try:
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error writing broker_config.json: {e}")

    def get_all_credentials(self) -> Dict:
        """Get all credentials at once."""
        return {
            'anthropic_key': self.get_anthropic_key(),
            'xai_key': self.get_xai_key(),
            'schwab': self.get_schwab_credentials(),
            'alpaca': self.get_alpaca_credentials(),
            'broker_config': self.get_broker_config(),
        }

    def check_status(self) -> Dict[str, bool]:
        """
        Check which credentials are available.

        Returns:
            Dictionary indicating which credentials are set
        """
        anthropic_key = self.get_anthropic_key()
        xai_key = self.get_xai_key()
        schwab = self.get_schwab_credentials()
        alpaca = self.get_alpaca_credentials()

        return {
            'anthropic': anthropic_key is not None,
            'xai': xai_key is not None,
            'schwab_app_key': schwab['app_key'] is not None,
            'schwab_app_secret': schwab['app_secret'] is not None,
            'schwab_refresh_token': schwab['refresh_token'] is not None,
            'schwab_complete': all(schwab.values()),
            'alpaca_api_key': alpaca['api_key'] is not None,
            'alpaca_secret_key': alpaca['secret_key'] is not None,
            'alpaca_complete': all(alpaca.values()),
        }

    def get_missing_schwab_credentials(self) -> list:
        """
        Get list of missing Schwab credentials.

        Returns:
            List of missing credential names
        """
        schwab = self.get_schwab_credentials()
        missing = []
        if not schwab['app_key']:
            missing.append('SCHWAB_APP_KEY')
        if not schwab['app_secret']:
            missing.append('SCHWAB_APP_SECRET')
        if not schwab['refresh_token']:
            missing.append('SCHWAB_REFRESH_TOKEN')
        return missing

    def create_example_files(self):
        """Create example credential files with instructions."""
        examples = {
            "anthropic_key.txt.example": "# Paste your Anthropic API key here (starts with sk-ant-)\n# Get it from: https://console.anthropic.com/\n# Then rename this file to: anthropic_key.txt\n",
            "schwab_app_key.txt.example": "# Paste your Schwab App Key here\n# Get it from: https://developer.schwab.com/\n# Then rename this file to: schwab_app_key.txt\n",
            "schwab_app_secret.txt.example": "# Paste your Schwab App Secret here\n# Get it from: https://developer.schwab.com/\n# Then rename this file to: schwab_app_secret.txt\n",
            "schwab_refresh_token.txt.example": "# Paste your Schwab Refresh Token here\n# Get it by running: python brokers/schwab_broker.py setup\n# Then rename this file to: schwab_refresh_token.txt\n",
            "alpaca_api_key.txt.example": "# Paste your Alpaca API Key here\n# Get it from: https://alpaca.markets/\n# Then rename this file to: alpaca_api_key.txt\n",
            "alpaca_secret_key.txt.example": "# Paste your Alpaca Secret Key here\n# Get it from: https://alpaca.markets/\n# Then rename this file to: alpaca_secret_key.txt\n",
        }

        for filename, content in examples.items():
            filepath = self.config_dir / filename
            if not filepath.exists():
                with open(filepath, 'w') as f:
                    f.write(content)


# Singleton instance
_credentials_manager = None

def get_credentials_manager() -> CredentialsManager:
    """Get the global credentials manager instance."""
    global _credentials_manager
    if _credentials_manager is None:
        _credentials_manager = CredentialsManager()
    return _credentials_manager


def main():
    """CLI for managing credentials."""
    import sys

    manager = get_credentials_manager()

    if len(sys.argv) < 2:
        print("Credentials Manager")
        print()
        print("Usage:")
        print("  python credentials_manager.py status          - Check which credentials are set")
        print("  python credentials_manager.py setup           - Interactive setup")
        print("  python credentials_manager.py create-examples - Create example credential files")
        print()
        return

    command = sys.argv[1]

    if command == "status":
        print("Credential Status:")
        print()

        status = manager.check_status()

        print(f"Anthropic API Key:       {'[OK] Set' if status['anthropic'] else '[X] Not set'}")
        print()
        print(f"Schwab App Key:          {'[OK] Set' if status['schwab_app_key'] else '[X] Not set'}")
        print(f"Schwab App Secret:       {'[OK] Set' if status['schwab_app_secret'] else '[X] Not set'}")
        print(f"Schwab Refresh Token:    {'[OK] Set' if status['schwab_refresh_token'] else '[X] Not set'}")
        print(f"Schwab (All):            {'[OK] Complete' if status['schwab_complete'] else '[X] Incomplete'}")
        print()
        print(f"Alpaca API Key:          {'[OK] Set' if status['alpaca_api_key'] else '[X] Not set'}")
        print(f"Alpaca Secret Key:       {'[OK] Set' if status['alpaca_secret_key'] else '[X] Not set'}")
        print(f"Alpaca (All):            {'[OK] Complete' if status['alpaca_complete'] else '[X] Incomplete'}")
        print()

        broker_config = manager.get_broker_config()
        print("Broker Configuration:")
        print(f"  Default Broker:        {broker_config['broker']}")
        print(f"  Paper Trading:         {broker_config['paper_trading']}")
        print(f"  Starting Cash:         ${broker_config['starting_cash']:,.2f}")

    elif command == "setup":
        print("Interactive Credentials Setup")
        print("=" * 60)
        print()
        print("Press Enter to skip any field")
        print()

        # Anthropic
        anthropic = input("Anthropic API Key (sk-ant-...): ").strip()
        if anthropic:
            manager.set_anthropic_key(anthropic)
            print("[OK] Saved")

        print()

        # Schwab
        print("Schwab Credentials:")
        schwab_key = input("  App Key: ").strip()
        schwab_secret = input("  App Secret: ").strip()
        schwab_token = input("  Refresh Token: ").strip()

        if schwab_key or schwab_secret or schwab_token:
            manager.set_schwab_credentials(
                app_key=schwab_key or None,
                app_secret=schwab_secret or None,
                refresh_token=schwab_token or None
            )
            print("[OK] Saved")

        print()

        # Alpaca
        print("Alpaca Credentials:")
        alpaca_key = input("  API Key: ").strip()
        alpaca_secret = input("  Secret Key: ").strip()

        if alpaca_key or alpaca_secret:
            manager.set_alpaca_credentials(
                api_key=alpaca_key or None,
                secret_key=alpaca_secret or None
            )
            print("[OK] Saved")

        print()

        # Broker config
        print("Broker Configuration:")
        broker = input("  Default broker (alpaca/schwab) [alpaca]: ").strip() or "alpaca"
        paper = input("  Paper trading? (yes/no) [yes]: ").strip().lower()
        paper_trading = paper != "no"
        cash = input("  Starting cash [$35,000]: ").strip()
        starting_cash = float(cash) if cash else 35000.0

        manager.set_broker_config(
            broker=broker,
            paper_trading=paper_trading,
            starting_cash=starting_cash
        )
        print("[OK] Saved")

        print()
        print("Setup complete!")
        print()
        print(f"Credentials saved to: {manager.config_dir}")

    elif command == "create-examples":
        manager.create_example_files()
        print(f"Example files created in: {manager.config_dir}")
        print()
        print("Edit the .example files and rename them (remove .example) to activate.")

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
