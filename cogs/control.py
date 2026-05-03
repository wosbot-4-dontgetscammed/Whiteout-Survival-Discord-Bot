import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from datetime import datetime
import os

from .config import LEVEL_MAPPING
from .database import DatabaseManager
from .log_config import get_logger
from .wos_api import fetch_player_info

logger = get_logger("control")

level_mapping = LEVEL_MAPPING

class Control(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        db = DatabaseManager.instance()
        self.conn_alliance = db.get("alliance")
        self.conn_users = db.get("users")
        self.conn_changes = db.get("changes")

        self.conn_settings = db.get("settings")
        cursor_settings = self.conn_settings.cursor()
        cursor_settings.execute("""
            CREATE TABLE IF NOT EXISTS auto (
                id INTEGER PRIMARY KEY,
                value INTEGER DEFAULT 1
            )
        """)

        cursor_settings.execute("SELECT COUNT(*) FROM auto")
        row = cursor_settings.fetchone()
        if row and row[0] == 0:
            cursor_settings.execute("INSERT INTO auto (value) VALUES (1)")
        self.conn_settings.commit()
        
        self.db_lock = asyncio.Lock()
        self.proxies = self.load_proxies()
        self.alliance_tasks = {}
        self.is_running = {}
        self.monitor_started = False
        
        self.control_queue = asyncio.Queue()
        self.control_lock = asyncio.Lock()
        self.current_control = None

    async def cog_unload(self):
        self.monitor_alliance_changes.cancel()
        if hasattr(self, '_queue_processor_task') and not self._queue_processor_task.done():
            self._queue_processor_task.cancel()
        for task in self.alliance_tasks.values():
            if not task.done():
                task.cancel()
        self.alliance_tasks.clear()
        self.is_running.clear()

    def load_proxies(self):
        proxies = []
        if os.path.exists('proxy.txt'):
            with open('proxy.txt', 'r') as f:
                proxies = [f"socks4://{line.strip()}" for line in f if line.strip()]
        return proxies

    async def fetch_user_data(self, fid, proxy=None):
        return await fetch_player_info(fid, proxy=proxy)

    async def check_agslist(self, channel, alliance_id):
        async with self.db_lock:
            cursor_users = self.conn_users.cursor()
            cursor_users.execute("SELECT fid, nickname, furnace_lv, stove_lv_content, kid FROM users WHERE alliance = ?", (alliance_id,))
            users = cursor_users.fetchall()

            if not users:
                return

        total_users = len(users)
        checked_users = 0

        async with self.db_lock:
            cursor_alliance = self.conn_alliance.cursor()
            cursor_alliance.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
            row = cursor_alliance.fetchone()
            alliance_name = row[0] if row else "Unknown"

        start_time = datetime.now()
        logger.info(f"{alliance_name} Alliance Control started at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        async with self.db_lock:
            settings_db = DatabaseManager.instance().get("settings")
            cursor = settings_db.cursor()
            cursor.execute("SELECT value FROM auto LIMIT 1")
            result = cursor.fetchone()
            auto_value = result[0] if result else 1
        
        
        embed = discord.Embed(
            title=f"🏰 {alliance_name} Alliance Control",
            description="🔍 Checking for changes in member status...",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="📊 Status",
            value=f"⏳ Control started at {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            inline=False
        )
        embed.add_field(
            name="📈 Progress",
            value=f"✨ Members checked: {checked_users}/{total_users}",
            inline=False
        )
        embed.set_footer(text="⚡ Automatic Alliance Control System")
        
        message = None
        if auto_value == 1:
            message = await channel.send(embed=embed)

        furnace_changes, nickname_changes, kid_changes = [], [], []

        i = 0
        while i < total_users:
            batch_users = users[i:i+20]
            for fid, old_nickname, old_furnace_lv, old_stove_lv_content, old_kid in batch_users:
                data = await self.fetch_user_data(fid)
                
                if data == 429 and (not os.path.exists('proxy.txt') or not self.proxies):
                    embed.description = f"⚠️ API Rate Limit! Waiting 60 seconds...\n📊 Progress: {checked_users}/{total_users} members"
                    embed.color = discord.Color.orange()
                    if message:
                        await message.edit(embed=embed)
                    
                    await asyncio.sleep(60)
                    
                    embed.description = "🔍 Checking for changes in member status..."
                    embed.color = discord.Color.blue()
                    if message:
                        await message.edit(embed=embed)
                    data = await self.fetch_user_data(fid)
                
                if isinstance(data, dict) and isinstance(data.get('data'), dict):
                    user_data = data['data']
                    new_furnace_lv = user_data['stove_lv']
                    new_nickname = user_data['nickname'].strip()
                    new_kid = user_data.get('kid', 0)
                    new_stove_lv_content = user_data['stove_lv_content']
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    async with self.db_lock:
                        cursor_users = self.conn_users.cursor()
                        cursor_changes = self.conn_changes.cursor()

                        if new_stove_lv_content != old_stove_lv_content:
                            cursor_users.execute("UPDATE users SET stove_lv_content = ? WHERE fid = ?", (new_stove_lv_content, fid))
                            self.conn_users.commit()

                        if old_kid != new_kid:
                            kid_changes.append(f"👤 **{old_nickname}** has transferred to a new state\n🔄 Old State: `{old_kid}`\n🆕 New State: `{new_kid}`")
                            cursor_users.execute("UPDATE users SET kid = ? WHERE fid = ?", (new_kid, fid))
                            self.conn_users.commit()

                        if new_furnace_lv != old_furnace_lv:
                            new_furnace_display = level_mapping.get(new_furnace_lv, new_furnace_lv)
                            old_furnace_display = level_mapping.get(old_furnace_lv, old_furnace_lv)
                            cursor_changes.execute("INSERT INTO furnace_changes (fid, old_furnace_lv, new_furnace_lv, change_date) VALUES (?, ?, ?, ?)",
                                                         (fid, old_furnace_lv, new_furnace_lv, current_time))
                            self.conn_changes.commit()
                            cursor_users.execute("UPDATE users SET furnace_lv = ? WHERE fid = ?", (new_furnace_lv, fid))
                            self.conn_users.commit()
                            furnace_changes.append(f"👤 **{old_nickname}**\n🔥 `{old_furnace_display}` ➡️ `{new_furnace_display}`")

                        if new_nickname.strip().lower() != old_nickname.strip().lower():
                            cursor_changes.execute("INSERT INTO nickname_changes (fid, old_nickname, new_nickname, change_date) VALUES (?, ?, ?, ?)",
                                                         (fid, old_nickname, new_nickname, current_time))
                            self.conn_changes.commit()
                            cursor_users.execute("UPDATE users SET nickname = ? WHERE fid = ?", (new_nickname, fid))
                            self.conn_users.commit()
                            nickname_changes.append(f"📝 `{old_nickname}` ➡️ `{new_nickname}`")

                checked_users += 1
                embed.set_field_at(
                    1,
                    name="📈 Progress",
                    value=f"✨ Members checked: {checked_users}/{total_users}",
                    inline=False
                )
                if message:
                    await message.edit(embed=embed)

            i += 20

        end_time = datetime.now()
        duration = end_time - start_time

        if furnace_changes or nickname_changes or kid_changes:
            if furnace_changes:
                furnace_embed = discord.Embed(
                    title="🔥 Furnace Level Changes",
                    description="\n\n".join(furnace_changes),
                    color=discord.Color.orange()
                )
                furnace_embed.set_footer(text=f"📊 Total Changes: {len(furnace_changes)}")
                await channel.send(embed=furnace_embed)

            if nickname_changes:
                nickname_embed = discord.Embed(
                    title="📝 Nickname Changes",
                    description="\n".join(nickname_changes),
                    color=discord.Color.blue()
                )
                nickname_embed.set_footer(text=f"📊 Total Changes: {len(nickname_changes)}")
                await channel.send(embed=nickname_embed)

            if kid_changes:
                kid_embed = discord.Embed(
                    title="🌍 State Transfer Notifications",
                    description="\n\n".join(kid_changes),
                    color=discord.Color.green()
                )
                kid_embed.set_footer(text=f"📊 Total Changes: {len(kid_changes)}")
                await channel.send(embed=kid_embed)

            embed.color = discord.Color.green()
            embed.set_field_at(
                0,
                name="📊 Final Status",
                value=f"✅ Control completed with changes\n⏰ {end_time.strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )
            embed.add_field(
                name="⏱️ Duration",
                value=str(duration),
                inline=True
            )
            embed.add_field(
                name="📈 Total Changes",
                value=f"🔄 {len(furnace_changes) + len(nickname_changes) + len(kid_changes)} changes detected",
                inline=True
            )
        else:
            embed.color = discord.Color.green()
            embed.set_field_at(
                0,
                name="📊 Final Status",
                value=f"✅ Control completed successfully\n⏰ {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n📝 No changes detected",
                inline=False
            )
            embed.add_field(
                name="⏱️ Duration",
                value=str(duration),
                inline=True
            )

        if message:
            await message.edit(embed=embed)
        logger.info(f"{alliance_name} Alliance Control completed at {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{alliance_name} Alliance Total Duration: {duration}")

    async def send_embed(self, channel, title, description, color):
        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )
        embed.set_footer(text="🔄 Alliance Control System")
        await channel.send(embed=embed)

    async def process_control_queue(self):
        logger.info("Queue processor started")
        while True:
            try:
                control_task = await self.control_queue.get()
                channel = control_task['channel']
                alliance_id = control_task['alliance_id']
                is_manual = control_task.get('is_manual', False)
                
                logger.info(f"Processing alliance ID: {alliance_id} (Manual: {is_manual})")
                
                if self.current_control and not self.current_control.done():
                    await self.current_control
                
                logger.info(f"Starting control for alliance ID: {alliance_id}")

                async with self.db_lock:
                    cursor_alliance = self.conn_alliance.cursor()
                    cursor_alliance.execute("""
                        SELECT name FROM alliance_list
                        WHERE alliance_id = ?
                    """, (alliance_id,))
                    row = cursor_alliance.fetchone()
                    alliance_name = row[0] if row else "Unknown"

                    cursor_users = self.conn_users.cursor()
                    cursor_users.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                    row = cursor_users.fetchone()
                    member_count = row[0] if row else 0
                
                self.current_control = asyncio.create_task(self.check_agslist(channel, alliance_id))
                await self.current_control
                
                self.current_control = None
                self.control_queue.task_done()
                
                logger.info(f"Completed control for alliance ID: {alliance_id}")
                
                if not is_manual:
                    await asyncio.sleep(60)
                
            except Exception as e:
                logger.exception(f"Error in process_control_queue: {str(e)}")
                self.control_queue.task_done()

                error_embed = discord.Embed(
                    title="⚠️ Control Process Error",
                    description=f"An error occurred during the control process:\n```{str(e)}```",
                    color=discord.Color.red()
                )
                try:
                    await channel.send(embed=error_embed)
                except (discord.HTTPException, discord.NotFound) as send_err:
                    logger.debug("Failed to send error embed to channel: %s", send_err)

                if not is_manual:
                    await asyncio.sleep(60)

    async def schedule_alliance_check(self, channel, alliance_id, current_interval):
        try:
            await asyncio.sleep(current_interval * 60)
            
            while self.is_running.get(alliance_id, False):
                try:
                    async with self.db_lock:
                        cursor_alliance = self.conn_alliance.cursor()
                        cursor_alliance.execute("""
                            SELECT interval
                            FROM alliancesettings
                            WHERE alliance_id = ?
                        """, (alliance_id,))
                        result = cursor_alliance.fetchone()
                        
                        if not result or result[0] == 0:
                            logger.info(f"Stopping checks for alliance {alliance_id} - interval disabled")
                            self.is_running[alliance_id] = False
                            break
                        
                        new_interval = result[0]
                        if new_interval != current_interval:
                            logger.info(f"Interval changed for alliance {alliance_id}: {current_interval} -> {new_interval}")
                            self.is_running[alliance_id] = False
                            self.alliance_tasks[alliance_id] = asyncio.create_task(
                                self.schedule_alliance_check(channel, alliance_id, new_interval)
                            )
                            self.is_running[alliance_id] = True
                            break

                    await self.control_queue.put({
                        'channel': channel,
                        'alliance_id': alliance_id
                    })
                    
                    await asyncio.sleep(current_interval * 60)
                    
                except Exception as e:
                    logger.error(f"Error in schedule_alliance_check for alliance {alliance_id}: {e}")
                    await asyncio.sleep(60)
                    
        except Exception as e:
            logger.exception(f"Fatal error in schedule_alliance_check for alliance {alliance_id}: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.monitor_started:
            logger.info("Starting monitor and queue processor...")
            self._queue_processor_task = asyncio.create_task(self.process_control_queue())
            self.monitor_alliance_changes.start()
            await self.start_alliance_checks()
            self.monitor_started = True
            logger.info("Monitor and queue processor started successfully")

    async def start_alliance_checks(self):
        try:
            for task in self.alliance_tasks.values():
                if not task.done():
                    task.cancel()
            self.alliance_tasks.clear()
            self.is_running.clear()

            async with self.db_lock:
                cursor_alliance = self.conn_alliance.cursor()
                cursor_alliance.execute("""
                    SELECT alliance_id, channel_id, interval
                    FROM alliancesettings
                    WHERE interval > 0
                """)
                alliances = cursor_alliance.fetchall()

                if not alliances:
                    logger.info("No alliances with intervals found")
                    return

                logger.info(f"Found {len(alliances)} alliances with intervals")
                
                for alliance_id, channel_id, interval in alliances:
                    channel = self.bot.get_channel(channel_id)
                    if channel is not None:
                        logger.info(f"Starting initial check for alliance {alliance_id}")
                        await self.control_queue.put({
                            'channel': channel,
                            'alliance_id': alliance_id
                        })
                        
                        self.is_running[alliance_id] = True
                        self.alliance_tasks[alliance_id] = asyncio.create_task(
                            self.schedule_alliance_check(channel, alliance_id, interval)
                        )
                        
                        await asyncio.sleep(2)
                    else:
                        logger.warning(f"Channel not found for alliance {alliance_id}")

        except Exception as e:
            logger.exception(f"Error in start_alliance_checks: {e}")

    async def cog_load(self):
        try:
            logger.info("Cog loaded successfully")
        except Exception as e:
            logger.exception(f"Error in cog_load: {e}")

    @tasks.loop(minutes=1)
    async def monitor_alliance_changes(self):
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            async with self.db_lock:
                cursor_alliance = self.conn_alliance.cursor()
                cursor_alliance.execute("SELECT alliance_id, channel_id, interval FROM alliancesettings")
                current_settings = {
                    alliance_id: (channel_id, interval)
                    for alliance_id, channel_id, interval in cursor_alliance.fetchall()
                }

                for alliance_id, (channel_id, interval) in current_settings.items():
                    task_exists = alliance_id in self.alliance_tasks
                    
                    if interval == 0 and task_exists:
                        self.is_running[alliance_id] = False
                        if not self.alliance_tasks[alliance_id].done():
                            self.alliance_tasks[alliance_id].cancel()
                        del self.alliance_tasks[alliance_id]
                        continue

                    if interval > 0 and (not task_exists or self.alliance_tasks[alliance_id].done()):
                        channel = self.bot.get_channel(channel_id)
                        if channel is not None:
                            self.is_running[alliance_id] = True
                            self.alliance_tasks[alliance_id] = asyncio.create_task(
                                self.schedule_alliance_check(channel, alliance_id, interval)
                            )

                for alliance_id in list(self.alliance_tasks.keys()):
                    if alliance_id not in current_settings:
                        self.is_running[alliance_id] = False
                        if not self.alliance_tasks[alliance_id].done():
                            self.alliance_tasks[alliance_id].cancel()
                        del self.alliance_tasks[alliance_id]

        except Exception as e:
            logger.exception(f"Error in monitor_alliance_changes: {e}")

    @monitor_alliance_changes.before_loop
    async def before_monitor_alliance_changes(self):
        await self.bot.wait_until_ready()

    @monitor_alliance_changes.after_loop
    async def after_monitor_alliance_changes(self):
        if self.monitor_alliance_changes.failed():
            logger.error("Monitor alliance changes task failed. Restarting in 30s...")
            await asyncio.sleep(30)
            self.monitor_alliance_changes.restart()

async def setup(bot):
    control_cog = Control(bot)
    await bot.add_cog(control_cog)