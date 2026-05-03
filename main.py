import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import sys
import os
import subprocess
from dotenv import load_dotenv
load_dotenv()

from cogs.log_config import get_logger
logger = get_logger("main")

def check_and_install_requirements():
    required_packages = {
        'discord.py': 'discord.py',
        'colorama': 'colorama',
        'requests': 'requests',
        'aiohttp': 'aiohttp',
        'python-dotenv': 'python-dotenv',
        'aiohttp-socks': 'aiohttp-socks',
        'pytz': 'pytz',
        'pyzipper': 'pyzipper'
    }

    def install_package(package_name):
        try:
            logger.info(f"Installing {package_name}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
            logger.info(f"{package_name} installed successfully.")
            return True
        except subprocess.CalledProcessError:
            logger.error(f"Error installing {package_name}.")
            return False

    packages_to_install = []
    try:
        from importlib.metadata import distributions
        installed_packages = set()
        for dist in distributions():
            try:
                installed_packages.add(dist.metadata['Name'].lower().replace('-', '_'))
            except Exception:
                pass
    except Exception:
        installed_packages = set()

    for package, pip_name in required_packages.items():
        check_name = package.lower().replace('-', '_').replace('.', '_')
        if check_name not in installed_packages and package.lower() not in installed_packages:
            packages_to_install.append(pip_name)

    if packages_to_install:
        logger.info("Missing libraries detected. Starting installation...")
        for package in packages_to_install:
            success = install_package(package)
            if not success:
                logger.error(f"Some libraries could not be installed. Please run pip install {package} manually.")
                sys.exit(1)
        logger.info("All required libraries installed!")
        return True
    return False

if __name__ == "__main__":
    check_and_install_requirements()
    
    import discord
    from discord.ext import commands
    import sqlite3
    import requests
    import asyncio

    VERSION_URL = "https://raw.githubusercontent.com/Reloisback/Whiteout-Survival-Discord-Bot/refs/heads/main/autoupdateinfo.txt"

    def restart_bot():
        logger.warning("Restarting bot...")
        python = sys.executable
        os.execl(python, python, *sys.argv)

    def setup_version_table():
        try:
            with sqlite3.connect('db/settings.sqlite') as conn:
                cursor = conn.cursor()
                cursor.execute('''CREATE TABLE IF NOT EXISTS versions (
                    file_name TEXT PRIMARY KEY,
                    version TEXT,
                    is_main INTEGER DEFAULT 0
                )''')
                conn.commit()
                logger.info("Version table created successfully.")
        except Exception as e:
            logger.error(f"Error creating version table: {e}")

    async def check_and_update_files():
        try:
            response = await asyncio.to_thread(requests.get, VERSION_URL, timeout=15)
            if response.status_code == 200:
                source_url = "https://raw.githubusercontent.com/Reloisback/Whiteout-Survival-Discord-Bot/refs/heads/main"
                logger.info("Connected to GitHub successfully.")
            else:
                logger.error(f"Failed to connect to GitHub (HTTP {response.status_code})")
                return False

            if not os.path.exists('cogs'):
                os.makedirs('cogs')
                logger.info("cogs folder created")

            content = response.text.split('\n')
            documents = {}
            main_py_updated = False

            doc_section = False
            for line in content:
                if line.startswith("Documants;"):
                    doc_section = True
                    continue
                elif doc_section and line.startswith("Updated Info;"):
                    break
                elif doc_section and '=' in line:
                    file_name, version = [x.strip() for x in line.split('=', 1)]
                    documents[file_name] = version

            update_notes = []
            update_section = False
            for line in content:
                if line.startswith("Updated Info;"):
                    update_section = True
                    continue
                if update_section and line.strip():
                    update_notes.append(line.strip())

            updates_needed = []
            with sqlite3.connect('db/settings.sqlite') as conn:
                cursor = conn.cursor()
                
                for file_name, new_version in documents.items():
                    cursor.execute("SELECT version FROM versions WHERE file_name = ?", (file_name,))
                    current_file_version = cursor.fetchone()
                    
                    if not current_file_version:
                        updates_needed.append((file_name, new_version))
                        if file_name == 'main.py':
                            main_py_updated = True
                    elif current_file_version[0] != new_version:
                        updates_needed.append((file_name, new_version))
                        if file_name == 'main.py':
                            main_py_updated = True

                if updates_needed:
                    logger.warning("Updates available!")
                    logger.warning("If this is your first installation and you see File and No version, please update!")
                    logger.info("Files to update:")
                    for file_name, new_version in updates_needed:
                        cursor.execute("SELECT version FROM versions WHERE file_name = ?", (file_name,))
                        current = cursor.fetchone()
                        current_version = current[0] if current else "File and No Version"
                        logger.info(f"• {file_name}: {current_version} -> {new_version}")

                    logger.info("Update Notes:")
                    for note in update_notes:
                        logger.info(f"• {note}")

                    if main_py_updated:
                        logger.warning("NOTE: This update includes changes to main.py. Bot will restart after update.")

                    auto_update_env = os.getenv('AUTO_UPDATE')
                    if auto_update_env is not None:
                        auto_update = auto_update_env.lower() == 'true'
                        response = 'y' if auto_update else 'n'
                        logger.info(f"AUTO_UPDATE={'true' if auto_update else 'false'}, {'proceeding' if auto_update else 'skipping'}.")
                    elif sys.stdin.isatty():
                        response = input("\nDo you want to update now? (y/n): ").lower()
                    else:
                        logger.warning("Non-interactive mode, skipping update prompt. Set AUTO_UPDATE=true to auto-update.")
                        response = 'n'
                    if response == 'y':
                        needs_restart = False
                        
                        for file_name, new_version in updates_needed:
                            if file_name.strip() != 'main.py':
                                file_url = f"{source_url}/{file_name}"
                                file_response = await asyncio.to_thread(requests.get, file_url, timeout=15)
                                
                                if file_response.status_code == 200:
                                    dirname = os.path.dirname(file_name)
                                    if dirname:
                                        os.makedirs(dirname, exist_ok=True)
                                    content = file_response.text.rstrip('\n')
                                    with open(file_name, 'w', encoding='utf-8', newline='') as f:
                                        f.write(content)
                                    
                                    cursor.execute("""
                                        INSERT OR REPLACE INTO versions (file_name, version, is_main)
                                        VALUES (?, ?, ?)
                                    """, (file_name, new_version, 0))

                        if main_py_updated:
                            main_file_url = f"{source_url}/main.py"
                            main_response = await asyncio.to_thread(requests.get, main_file_url, timeout=15)
                            
                            if main_response.status_code == 200:
                                content = main_response.text.rstrip('\n')
                                with open('main.py.new', 'w', encoding='utf-8', newline='') as f:
                                    f.write(content)
                                
                                cursor.execute("""
                                    INSERT OR REPLACE INTO versions (file_name, version, is_main)
                                    VALUES (?, ?, 1)
                                """, ('main.py', documents['main.py']))
                                
                                needs_restart = True

                        conn.commit()
                        logger.info("All updates completed successfully!")

                        if needs_restart:
                            if os.path.exists('main.py.bak'):
                                os.remove('main.py.bak')
                            os.rename('main.py', 'main.py.bak')
                            os.rename('main.py.new', 'main.py')
                            logger.warning("Restarting bot to apply main.py updates...")
                            restart_bot()
                    else:
                        logger.warning("Update skipped. Running with existing files.")

            return False

        except Exception as e:
            logger.error(f"Error during version check: {e}")
            return False

    class CustomBot(commands.Bot):
        async def on_error(self, event_name, *args, **kwargs):
            if event_name == "on_interaction":
                error = sys.exc_info()[1]
                if isinstance(error, discord.NotFound) and error.code == 10062:
                    return
            
            await super().on_error(event_name, *args, **kwargs)

        async def on_command_error(self, ctx, error):
            if isinstance(error, discord.NotFound) and error.code == 10062:
                return
            await super().on_command_error(ctx, error)

    intents = discord.Intents.default()
    intents.message_content = True

    bot = CustomBot(command_prefix='/', intents=intents)

    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token:
        token_file = 'bot_token.txt'
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                bot_token = f.read().strip()
        else:
            bot_token = input("Enter the bot token: ")
    if not bot_token:
        logger.error("No bot token found. Set BOT_TOKEN in .env or provide bot_token.txt")
        sys.exit(1)

    from cogs.database import DatabaseManager
    from cogs.config import validate_config
    db = DatabaseManager.instance()

    validate_config()
    logger.info("Database connections have been successfully established.")

    def create_tables():
        db.get("changes").execute('''CREATE TABLE IF NOT EXISTS nickname_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fid INTEGER,
            old_nickname TEXT,
            new_nickname TEXT,
            change_date TEXT
        )''')
        db.get("changes").execute('''CREATE TABLE IF NOT EXISTS furnace_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fid INTEGER,
            old_furnace_lv INTEGER,
            new_furnace_lv INTEGER,
            change_date TEXT
        )''')
        db.get("changes").commit()

        db.get("settings").execute('''CREATE TABLE IF NOT EXISTS botsettings (
            id INTEGER PRIMARY KEY,
            channelid INTEGER,
            giftcodestatus TEXT
        )''')
        db.get("settings").execute('''CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY,
            is_initial INTEGER
        )''')
        db.get("settings").commit()

        db.get("users").execute('''CREATE TABLE IF NOT EXISTS users (
            fid INTEGER PRIMARY KEY,
            nickname TEXT,
            furnace_lv INTEGER DEFAULT 0,
            kid INTEGER,
            stove_lv_content TEXT,
            alliance TEXT
        )''')
        db.get("users").commit()

        db.get("giftcode").execute('''CREATE TABLE IF NOT EXISTS gift_codes (
            giftcode TEXT PRIMARY KEY,
            date TEXT
        )''')
        db.get("giftcode").execute('''CREATE TABLE IF NOT EXISTS user_giftcodes (
            fid INTEGER,
            giftcode TEXT,
            status TEXT,
            PRIMARY KEY (fid, giftcode),
            FOREIGN KEY (giftcode) REFERENCES gift_codes (giftcode)
        )''')
        db.get("giftcode").commit()

        db.get("alliance").execute('''CREATE TABLE IF NOT EXISTS alliancesettings (
            alliance_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            interval INTEGER
        )''')
        db.get("alliance").execute('''CREATE TABLE IF NOT EXISTS alliance_list (
            alliance_id INTEGER PRIMARY KEY,
            name TEXT
        )''')
        db.get("alliance").commit()

        logger.info("All tables checked.")

    create_tables()
    setup_version_table()

    async def load_cogs():
        extensions = [
            "cogs.control",
            "cogs.alliance",
            "cogs.alliance_member_operations",
            "cogs.bot_operations",
            "cogs.logsystem",
            "cogs.support_operations",
            "cogs.gift_operations",
            "cogs.changes",
            "cogs.w",
            "cogs.wel",
            "cogs.other_features",
            "cogs.bear_trap",
            "cogs.id_channel",
            "cogs.backup_operations",
            "cogs.bear_trap_editor",
            "cogs.gift_scraper",
        ]
        for ext in extensions:
            try:
                logger.info("Loading %s...", ext)
                await bot.load_extension(ext)
                logger.info("Loaded %s", ext)
            except Exception as e:
                logger.error("Failed to load %s: %s", ext, e)

    @bot.event
    async def on_ready():
        try:
            logger.info(f"Logged in as {bot.user}")
            synced = await bot.tree.sync()
        except Exception as e:
            logger.error(f"Error syncing commands: {e}")

    async def main():
        if check_and_install_requirements():
            logger.info("Library installations completed, starting bot...")

        await check_and_update_files()
        await load_cogs()
        try:
            await bot.start(bot_token)
        finally:
            db.close_all()
            logger.info("Database connections closed.")

    if __name__ == "__main__":
        asyncio.run(main())