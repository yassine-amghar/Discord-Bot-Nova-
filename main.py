import os
import random
import json
import time
import datetime
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncpg

load_dotenv()
TOKEN = os.getenv("TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=[".", "+"], intents=intents, help_command=None)

# ─────────────────────────────────────────────
# CHANNEL IDs
# ─────────────────────────────────────────────
GUIDELINES_ID         = 1482796651344040099
ANNOUNCE_ID           = 1482802123308531813
PERKS_ID              = 1482802267680804964
PROFILE_ID            = 1482802311578259497
SUPPORT_ID            = 1482802401844006932
HANGSPOT_ID           = 1482802842900103311
FAME_ID               = 1482802962198823093
CHAT_LB_ID            = 1482804897631043777
VOICE_LB_ID           = 1482804934331338772
LEVELS_ID             = 1482804659813875823
TRACKED_CHAT_CHANNELS = {1482802842900103311, 1482802872900103312}


# ─────────────────────────────────────────────
# POSTGRESQL DATABASE
# ─────────────────────────────────────────────
db_pool = None

async def init_db():
    global db_pool
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("❌ DATABASE_URL not set!")
        return
    # Fix SSL for Railway public URL
    if "railway" in database_url and "sslmode" not in database_url:
        database_url += "?sslmode=require"
    db_pool = await asyncpg.create_pool(
        database_url,
        min_size=1,
        max_size=5
    )
    async with db_pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(user_id TEXT PRIMARY KEY, data JSONB NOT NULL DEFAULT '{}')"
        )
    print("✅ PostgreSQL connected.")

async def _async_get_db():
    if not db_pool:
        return {}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, data FROM users")
        result = {}
    for row in rows:
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        result[row["user_id"]] = dict(data)
    return result

async def _async_save_db(data):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        for uid, udata in data.items():
            await conn.execute(
                "INSERT INTO users (user_id, data) VALUES ($1, $2::jsonb) "
                "ON CONFLICT (user_id) DO UPDATE SET data = $2::jsonb",
                uid, json.dumps(udata)
            )

async def get_db():
    return await _async_get_db()

async def save_db(data):
    await _async_save_db(data)

def ensure_user(data, user_id):
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    defaults = {
        "wallet": 0, "bank": 0, "credits": 0,
        "xp": 0, "level": 1, "prestige": 0,
        "messages": {}, "charms": 0,
        "booster_end": 0,
        "lootcrates": 0,
        "blacktea_wins": 0,
        "last_daily": 0, "last_weekly": 0, "last_work": 0, "last_rob": 0,
        "partner": None, "marry_date": 0,
        "inbox": [],
        "afk": None,
        "weekly_messages": 0,
        "voice_minutes": 0,
        "weekly_voice_minutes": 0,
        "voice_join_time": None,
    }
    for k, v in defaults.items():
        if k not in data[uid]:
            data[uid][k] = v
    return data

def parse_amount(s, balance):
    s = str(s).lower().strip()
    if s == "all":  return balance
    if s == "half": return balance // 2
    for suffix, mult in [("b", 1_000_000_000), ("m", 1_000_000), ("k", 1_000)]:
        if s.endswith(suffix):
            try: return int(float(s[:-1]) * mult)
            except: return None
    try: return int(s)
    except: return None

def get_multiplier(data, user_id):
    return 2 if data[str(user_id)].get("booster_end", 0) > time.time() else 1

def add_xp(data, user_id, amount):
    uid = str(user_id)
    data[uid]["xp"] += amount
    needed = (data[uid]["level"] + data[uid].get("prestige", 0) * 10) * 500
    if data[uid]["xp"] >= needed:
        data[uid]["level"] += 1
        data[uid]["xp"] = 0
        return True
    return False

snipe_cache  = {}
afk_cache    = {}
msg_cooldown = {}
voice_sessions = {}  # user_id -> join timestamp

# ─────────────────────────────────────────────
# COLOR ROLES
# ─────────────────────────────────────────────
COLOR_ROLES = {
    "Scarlet Fury":    "🔴",
    "Fire Pop":        "🟠",
    "Rose Dust":       "🌸",
    "Crimson Blaze":   "❤️",
    "Raspberry Burst": "🍇",
    "Blush Bloom":     "🌷",
    "Golden Ember":    "🟡",
    "Sunbeam Honey":   "🍯",
    "Apricot Glow":    "🍑",
    "Emerald Surge":   "💚",
    "Mint Breeze":     "🌿",
    "Frosted Mist":    "🩵",
    "Ocean Depth":     "🌊",
}

# ─────────────────────────────────────────────
# GIF HELPER
# ─────────────────────────────────────────────
async def fetch_gif(action: str) -> str:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://nekos.best/api/v2/{action}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["results"][0]["url"]
    except Exception:
        pass
    return ""

async def send_action(ctx, action: str, member: discord.Member, color: discord.Color):
    phrases = {
        "hug":      f"💞 **{ctx.author.display_name}** hugs **{member.display_name}**!",
        "kiss":     f"💋 **{ctx.author.display_name}** kisses **{member.display_name}**!",
        "dance":    f"💃 **{ctx.author.display_name}** dances with **{member.display_name}**!",
        "handhold": f"🤝 **{ctx.author.display_name}** holds hands with **{member.display_name}**!",
        "cry":      f"😢 **{ctx.author.display_name}** cries with **{member.display_name}**!",
        "bite":     f"😬 **{ctx.author.display_name}** bites **{member.display_name}**!",
        "poke":     f"👉 **{ctx.author.display_name}** pokes **{member.display_name}**!",
        "lick":     f"👅 **{ctx.author.display_name}** licks **{member.display_name}**!",
        "highfive": f"🙌 **{ctx.author.display_name}** high-fives **{member.display_name}**!",
        "slap":     f"👋 **{ctx.author.display_name}** slaps **{member.display_name}**!",
        "cuddle":   f"🫂 **{ctx.author.display_name}** cuddles **{member.display_name}**!",
        "kill":     f"⚔️ **{ctx.author.display_name}** kills **{member.display_name}**!",
    }
    gif = await fetch_gif(action)
    embed = discord.Embed(description=phrases.get(action, f"**{ctx.author.display_name}** → **{member.display_name}**"), color=color)
    if gif:
        embed.set_image(url=gif)
    else:
        embed.set_footer(text="(GIF unavailable)")
    await ctx.send(embed=embed)

# ─────────────────────────────────────────────
# PERSISTENT VIEWS
# ─────────────────────────────────────────────
class GuidelinesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Get your roles here", style=discord.ButtonStyle.primary, emoji="🌹", custom_id="goto_profile")
    async def profile_btn(self, interaction, btn):
        await interaction.response.send_message(f"Head over to <#1482802311578259497> to get your roles!", ephemeral=True)

    @discord.ui.button(label="Server Perks", style=discord.ButtonStyle.secondary, emoji="🎲", custom_id="goto_perks")
    async def perks_btn(self, interaction, btn):
        await interaction.response.send_message(f"Check out <#1482802267680804964> to see all server perks!", ephemeral=True)

    @discord.ui.button(label="Done reading? Check out 💬・hangspot →", style=discord.ButtonStyle.success, custom_id="goto_hangspot")
    async def hangspot_btn(self, interaction, btn):
        await interaction.response.send_message(f"Head over to <#1482802842900103311>! 🎉", ephemeral=True)


class GenderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="1. Female", style=discord.ButtonStyle.secondary, emoji="🌸", custom_id="role_female")
    async def female_btn(self, interaction, btn):
        role      = discord.utils.get(interaction.guild.roles, name="Female")
        male_role = discord.utils.get(interaction.guild.roles, name="Male")
        if not role:
            return await interaction.response.send_message("❌ `Female` role not found.", ephemeral=True)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            return await interaction.response.send_message("🌸 Removed your **Female** role.", ephemeral=True)
        if male_role and male_role in interaction.user.roles:
            await interaction.user.remove_roles(male_role)
        await interaction.user.add_roles(role)
        await interaction.response.send_message("🌸 You now have the **Female** role!", ephemeral=True)

    @discord.ui.button(label="2. Male", style=discord.ButtonStyle.secondary, emoji="💙", custom_id="role_male")
    async def male_btn(self, interaction, btn):
        role        = discord.utils.get(interaction.guild.roles, name="Male")
        female_role = discord.utils.get(interaction.guild.roles, name="Female")
        if not role:
            return await interaction.response.send_message("❌ `Male` role not found.", ephemeral=True)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            return await interaction.response.send_message("💙 Removed your **Male** role.", ephemeral=True)
        if female_role and female_role in interaction.user.roles:
            await interaction.user.remove_roles(female_role)
        await interaction.user.add_roles(role)
        await interaction.response.send_message("💙 You now have the **Male** role!", ephemeral=True)


class ColorSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name, emoji=emoji, value=name)
            for name, emoji in COLOR_ROLES.items()
        ]
        super().__init__(placeholder="🎨 Choose your color...", min_values=1, max_values=1, options=options, custom_id="color_select")

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        roles_to_remove = [discord.utils.get(interaction.guild.roles, name=name) for name in COLOR_ROLES]
        roles_to_remove = [r for r in roles_to_remove if r and r in interaction.user.roles]
        if roles_to_remove:
            await interaction.user.remove_roles(*roles_to_remove)
        new_role = discord.utils.get(interaction.guild.roles, name=chosen)
        if not new_role:
            return await interaction.response.send_message(f"❌ Role `{chosen}` not found. Ask an admin to create it.", ephemeral=True)
        await interaction.user.add_roles(new_role)
        await interaction.response.send_message(f"{COLOR_ROLES[chosen]} You now have the **{chosen}** color role!", ephemeral=True)


class ColorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ColorSelect())


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 General Support", style=discord.ButtonStyle.primary, custom_id="open_ticket_general")
    async def open_general(self, interaction, btn):
        await create_ticket(interaction, "general")

    @discord.ui.button(label="🚨 Report a User", style=discord.ButtonStyle.danger, custom_id="open_ticket_report")
    async def open_report(self, interaction, btn):
        await create_ticket(interaction, "report")


async def create_ticket(interaction: discord.Interaction, ticket_type: str):
    guild  = interaction.guild
    user   = interaction.user
    prefix = "ticket" if ticket_type == "general" else "report"
    existing = discord.utils.get(guild.text_channels, name=f"{prefix}-{user.name.lower()}")
    if existing:
        return await interaction.response.send_message(f"❌ You already have an open ticket: {existing.mention}", ephemeral=True)

    staff_role = discord.utils.get(guild.roles, name="Staff")
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user:               discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    category = discord.utils.get(guild.categories, name="🎫 Tickets")
    if not category:
        category = await guild.create_category("🎫 Tickets")

    channel = await guild.create_text_channel(
        name=f"{prefix}-{user.name.lower()}",
        category=category,
        overwrites=overwrites
    )

    if ticket_type == "general":
        title = "🎫 General Support"
        desc  = f"Welcome {user.mention}!\n\nPlease describe your issue and a staff member will assist you shortly."
        color = 0xFF85A1
    else:
        title = "🚨 Report a User"
        desc  = (
            f"Welcome {user.mention}!\n\nPlease provide:\n"
            "**1.** The username of the person you're reporting\n"
            "**2.** What happened\n"
            "**3.** Any evidence (screenshots etc.)"
        )
        color = 0xFF0000

    embed = discord.Embed(title=title, description=desc, color=color)
    embed.set_footer(text="Hang Spot Support • Staff will be with you shortly")
    await channel.send(embed=embed, view=CloseTicketView())
    await interaction.response.send_message(f"✅ Your ticket has been created: {channel.mention}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction, btn):
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# ─────────────────────────────────────────────
# HELP MENU
# ─────────────────────────────────────────────
class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    def main_embed(self):
        embed = discord.Embed(
            title="📖  Help Menu",
            description=(
                "Use the buttons below to explore each category.\n\n"
                "**Categories**\n"
                "🔹 **General** — Profile, rank, marriage, leaderboards & more\n"
                "💰 **Economy** — Earn, gamble, rob, duel & manage money\n"
                "🎭 **Fun** — Anime roleplay actions & social games\n\n"
                "`< >` required  •  `[ ]` optional"
            ),
            color=0x5865F2
        )
        embed.set_footer(text="Select a category below")
        return embed

    def general_embed(self):
        cmds = [
            ("`.credits`",                       "View your credits"),
            ("`.charms [member]`",               "View charm count"),
            ("`.leaderboard [category] [page]`", "View leaderboards"),
            ("`.boosters`",                      "View active boosters"),
            ("`.rank [member]`",                 "View rank & XP"),
            ("`.prestige`",                      "Prestige at Level 50"),
            ("`.blacktea`",                      "Word unscramble game"),
            ("`+charm <member>`",                "Give a charm"),
            ("`.messages [member]`",             "Message count per channel"),
            ("`.snipe`",                         "Last deleted/edited message"),
            ("`.afk [reason]`",                  "Set yourself as AFK"),
            ("`.partner [member]`",              "View marriage details"),
            ("`.marry <member>`",                "Propose marriage"),
            ("`.divorce`",                       "Divorce your spouse"),
        ]
        embed = discord.Embed(title="🔹 General Commands", color=0x3498DB)
        embed.description = "\n".join(f"**{c}** — {d}" for c, d in cmds)
        return embed

    def economy_embed(self):
        cmds = [
            ("`.balance [member]`",         "Check balance"),
            ("`.deposit <amount>`",         "Wallet → Bank"),
            ("`.withdraw <amount>`",        "Bank → Wallet"),
            ("`.give <member> <amount>`",   "Give money"),
            ("`.work`",                     "Work for money"),
            ("`.daily`",                    "Daily reward"),
            ("`.weekly`",                   "Weekly reward"),
            ("`.rob <member>`",             "Rob someone"),
            ("`.cooldowns [member]`",       "Check cooldowns"),
            ("`.inbox [page]`",             "View inbox"),
            ("`.coinflip <amount> <h|t>`",  "Flip a coin"),
            ("`.blackjack <amount>`",       "Play blackjack"),
            ("`.roulette <amount> <type>`", "Play roulette"),
            ("`.rps <member> <amount>`",    "Rock Paper Scissors"),
            ("`.duel <member> <amount>`",   "50/50 duel"),
            ("`.ttt <member> <amount>`",    "Tic-Tac-Toe"),
        ]
        embed = discord.Embed(title="💰 Economy Commands", color=0xF1C40F)
        embed.description = "\n".join(f"**{c}** — {d}" for c, d in cmds)
        return embed

    def fun_embed(self):
        cmds = [
            ("`.hug <member>`",             "Hug someone"),
            ("`.kiss <member>`",            "Kiss someone"),
            ("`.dance <member>`",           "Dance with someone"),
            ("`.handhold <member>`",        "Hold hands"),
            ("`.cry <member>`",             "Cry together"),
            ("`.bite <member>`",            "Bite someone"),
            ("`.poke <member>`",            "Poke someone"),
            ("`.lick <member>`",            "Lick someone"),
            ("`.highfive <member>`",        "High-five someone"),
            ("`.slap <member>`",            "Slap someone"),
            ("`.cuddle <member>`",          "Cuddle someone"),
            ("`.kill <member>`",            "Kill someone"),
            ("`.bestie <member> [member]`", "Bestie compatibility"),
            ("`.aura [member]`",            "Check aura"),
            ("`.ship <member> [member]`",   "Ship two members"),
        ]
        embed = discord.Embed(title="🎭 Fun Commands", color=0xE91E63)
        embed.description = "\n".join(f"**{c}** — {d}" for c, d in cmds)
        return embed

    @discord.ui.button(label="General",  style=discord.ButtonStyle.primary,   emoji="🔹")
    async def general_btn(self, interaction, btn):
        await interaction.response.edit_message(embed=self.general_embed(), view=self)

    @discord.ui.button(label="Economy",  style=discord.ButtonStyle.success,   emoji="💰")
    async def economy_btn(self, interaction, btn):
        await interaction.response.edit_message(embed=self.economy_embed(), view=self)

    @discord.ui.button(label="Fun",      style=discord.ButtonStyle.secondary, emoji="🎭")
    async def fun_btn(self, interaction, btn):
        await interaction.response.edit_message(embed=self.fun_embed(), view=self)

    @discord.ui.button(label="Back",     style=discord.ButtonStyle.danger,    emoji="↩️")
    async def back_btn(self, interaction, btn):
        await interaction.response.edit_message(embed=self.main_embed(), view=self)

# ─────────────────────────────────────────────
# LEADERBOARD HELPERS
# ─────────────────────────────────────────────
def build_chat_lb_embed(data):
    embed = discord.Embed(
        title="💬 Weekly Chat Leaderboard",
        description="Top 10 most active chatters in **hangspot** & **chillspot** this week!",
        color=0xFF85A1
    )
    sorted_users = sorted(data.items(), key=lambda x: x[1].get("weekly_messages", 0), reverse=True)[:10]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []
    for i, (uid, u) in enumerate(sorted_users, start=1):
        badge = medals.get(i, f"**{i}.**")
        lines.append(f"{badge} <@{uid}> — **{u.get('weekly_messages', 0):,}** messages")
    embed.description += "\n\n" + ("\n".join(lines) if lines else "No data yet.")
    embed.set_footer(text=f"Resets every Monday • Last updated: {datetime.datetime.utcnow().strftime('%b %d, %Y %H:%M')} UTC")
    return embed

def build_voice_lb_embed(data):
    embed = discord.Embed(
        title="🔊 Weekly Voice Leaderboard",
        description="Top 10 most active voice users this week!",
        color=0x9B59B6
    )
    sorted_users = sorted(data.items(), key=lambda x: x[1].get("weekly_voice_minutes", 0), reverse=True)[:10]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []
    for i, (uid, u) in enumerate(sorted_users, start=1):
        badge  = medals.get(i, f"**{i}.**")
        mins   = u.get("weekly_voice_minutes", 0)
        hours  = mins // 60
        minutes = mins % 60
        lines.append(f"{badge} <@{uid}> — **{hours}h {minutes}m**")
    embed.description += "\n\n" + ("\n".join(lines) if lines else "No data yet.")
    embed.set_footer(text=f"Resets every Monday • Last updated: {datetime.datetime.utcnow().strftime('%b %d, %Y %H:%M')} UTC")
    return embed

# Stored message IDs for leaderboard embeds
lb_message_ids = {"chat": None, "voice": None}

# ─────────────────────────────────────────────
# BACKGROUND TASKS
# ─────────────────────────────────────────────
@tasks.loop(minutes=5)
async def update_leaderboards():
    data = await get_db()
    # Update chat leaderboard
    chat_channel = bot.get_channel(CHAT_LB_ID)
    if chat_channel:
        embed = build_chat_lb_embed(data)
        if lb_message_ids["chat"]:
            try:
                msg = await chat_channel.fetch_message(lb_message_ids["chat"])
                await msg.edit(embed=embed)
            except Exception:
                msg = await chat_channel.send(embed=embed)
                lb_message_ids["chat"] = msg.id
        else:
            # Find existing bot message
            async for msg in chat_channel.history(limit=10):
                if msg.author == bot.user:
                    lb_message_ids["chat"] = msg.id
                    await msg.edit(embed=embed)
                    break
            else:
                msg = await chat_channel.send(embed=embed)
                lb_message_ids["chat"] = msg.id

    # Update voice leaderboard
    voice_channel = bot.get_channel(VOICE_LB_ID)
    if voice_channel:
        embed = build_voice_lb_embed(data)
        if lb_message_ids["voice"]:
            try:
                msg = await voice_channel.fetch_message(lb_message_ids["voice"])
                await msg.edit(embed=embed)
            except Exception:
                msg = await voice_channel.send(embed=embed)
                lb_message_ids["voice"] = msg.id
        else:
            async for msg in voice_channel.history(limit=10):
                if msg.author == bot.user:
                    lb_message_ids["voice"] = msg.id
                    await msg.edit(embed=embed)
                    break
            else:
                msg = await voice_channel.send(embed=embed)
                lb_message_ids["voice"] = msg.id

@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))  # midnight UTC
async def weekly_reset():
    # Only run on Sundays (weekday 6)
    if datetime.datetime.utcnow().weekday() != 6:
        return

    data = await get_db()

    PRINCE_ROLE_ID    = 1483096301007667332
    PRINCESS_ROLE_ID  = 1483096038989627564

    for guild in bot.guilds:
        prince_role   = guild.get_role(PRINCE_ROLE_ID)
        princess_role = guild.get_role(PRINCESS_ROLE_ID)
        male_role     = discord.utils.get(guild.roles, name="Male")
        female_role   = discord.utils.get(guild.roles, name="Female")

        # --- Remove old Prince/Princess roles and reset nicknames ---
        if prince_role:
            for member in prince_role.members:
                try:
                    await member.remove_roles(prince_role)
                    # Reset nickname - remove the 🤴 emoji
                    if member.nick and "🤴" in member.nick:
                        new_nick = member.nick.replace("🤴", "").strip()
                        await member.edit(nick=new_nick if new_nick else None)
                except discord.Forbidden:
                    pass

        if princess_role:
            for member in princess_role.members:
                try:
                    await member.remove_roles(princess_role)
                    # Reset nickname - remove the 👸 emoji
                    if member.nick and "👸" in member.nick:
                        new_nick = member.nick.replace("👸", "").strip()
                        await member.edit(nick=new_nick if new_nick else None)
                except discord.Forbidden:
                    pass

        # --- Find top male and female chatters ---
        top_male   = None
        top_female = None
        top_male_msgs   = 0
        top_female_msgs = 0

        for uid, u in data.items():
            weekly_msgs = u.get("weekly_messages", 0)
            if weekly_msgs == 0:
                continue
            member = guild.get_member(int(uid))
            if not member:
                continue
            if male_role and male_role in member.roles:
                if weekly_msgs > top_male_msgs:
                    top_male_msgs = weekly_msgs
                    top_male = member
            elif female_role and female_role in member.roles:
                if weekly_msgs > top_female_msgs:
                    top_female_msgs = weekly_msgs
                    top_female = member

        # --- Assign Prince ---
        if top_male and prince_role:
            try:
                await top_male.add_roles(prince_role)
                current_nick = top_male.nick or top_male.name
                await top_male.edit(nick=f"{current_nick} 🤴")
            except discord.Forbidden:
                pass

        # --- Assign Princess ---
        if top_female and princess_role:
            try:
                await top_female.add_roles(princess_role)
                current_nick = top_female.nick or top_female.name
                await top_female.edit(nick=f"{current_nick} 👸")
            except discord.Forbidden:
                pass

        # --- Announce in levels channel ---
        levels_channel = bot.get_channel(LEVELS_ID)
        if levels_channel:
            embed = discord.Embed(
                title="👑 Weekly Royalty",
                description="The weekly Prince & Princess have been crowned!",
                color=0xFFD700
            )
            if top_male:
                embed.add_field(name="🤴 Prince", value=f"{top_male.mention} — **{top_male_msgs:,}** messages", inline=True)
            if top_female:
                embed.add_field(name="👸 Princess", value=f"{top_female.mention} — **{top_female_msgs:,}** messages", inline=True)
            await levels_channel.send(embed=embed)

    # --- Reset weekly stats ---
    for uid in data:
        data[uid]["weekly_messages"]      = 0
        data[uid]["weekly_voice_minutes"] = 0
    await save_db(data)
    print("✅ Weekly reset complete — Prince & Princess assigned.")

@bot.event
async def on_ready():
    await init_db()
    print(f"✅ {bot.user} is online | Prefixes: . and +")
    bot.add_view(GuidelinesView())
    bot.add_view(GenderView())
    bot.add_view(ColorView())
    bot.add_view(TicketView())
    bot.add_view(CloseTicketView())
    update_leaderboards.start()
    weekly_reset.start()
    blacktea_scheduler.start()
    await load_words()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    uid  = str(message.author.id)
    now  = time.time()
    chan = message.channel.id

    if uid in afk_cache:
        del afk_cache[uid]
        data = await get_db(); ensure_user(data, message.author.id)
        data[uid]["afk"] = None; await save_db(data)
        try:
            await message.channel.send(f"👋 Welcome back, {message.author.mention}! AFK removed.", delete_after=5)
        except Exception:
            pass

    for mention in message.mentions:
        mid = str(mention.id)
        if mid in afk_cache:
            reason = afk_cache[mid] or "No reason given"
            try:
                await message.channel.send(f"💤 **{mention.display_name}** is AFK: {reason}", delete_after=8)
            except Exception:
                pass

    if uid not in msg_cooldown or now - msg_cooldown[uid] > 30:
        data = await get_db(); ensure_user(data, message.author.id)
        data[uid]["credits"] = data[uid].get("credits", 0) + 5
        data[uid]["messages"][str(chan)] = data[uid]["messages"].get(str(chan), 0) + 1

        # Weekly chat leaderboard tracking
        if chan in TRACKED_CHAT_CHANNELS:
            data[uid]["weekly_messages"] = data[uid].get("weekly_messages", 0) + 1

        if add_xp(data, message.author.id, 20):
            new_level = data[uid]["level"]
            # Post level up in levels channel
            levels_channel = bot.get_channel(LEVELS_ID)
            if levels_channel:
                rewards = get_level_rewards(new_level)
                embed = discord.Embed(
                    title="🎊 Level Up!",
                    description=(
                        f"{message.author.mention} leveled up to **Level {new_level}**! 🎉\n\n"
                        f"{rewards}"
                    ),
                    color=0xFFD700
                )
                embed.set_thumbnail(url=message.author.display_avatar.url)
                await levels_channel.send(embed=embed)
                await assign_milestone_roles(message.author, new_level)
            else:
                try:
                    await message.channel.send(f"🎊 {message.author.mention} leveled up to **Level {new_level}**!")
                except Exception:
                    pass

        await save_db(data)
        msg_cooldown[uid] = now

    await bot.process_commands(message)

def get_level_rewards(level):
    rewards = {
        5:   "🎁 **Reward:** Unlocked Streaming / Camera in voice channels!",
        10:  "🎁 **Reward:** Unlocked Media Channel Posting!",
        20:  "🎁 **Reward:** Unlocked External Emojis!",
        30:  "🎁 **Reward:** Unlocked GIFs!",
        40:  "🎁 **Reward:** Unlocked Color Panel!",
        50:  "🎁 **Reward:** Unlocked External Stickers!",
        60:  "🎁 **Reward:** Unlocked Post Images Anywhere!",
        70:  "🎁 **Reward:** +5 Credits per charm given!",
        80:  "🎁 **Reward:** Unlocked Soundboards & Voice Messages!",
        90:  "🎁 **Reward:** Unlocked External Sounds!",
        100: "🎁 **Reward:** Unlocked **PRESTIGE**! Use `.prestige` to reset and earn a prestige badge!",
    }
    return rewards.get(level, f"🎁 **Reward:** +500 Credits bonus!")


# Milestone role IDs
MILESTONE_ROLES = {
    5:  1482901111852630260,
    10: 1482903152821801113,
    20: 1482903265174753523,
    30: 1482903519437656166,
    50: 1482903618263973938,
    60: 1482903765869789244,
    80: 1482904289490763776,
    90: 1482904501760430150,
}

async def assign_milestone_roles(member: discord.Member, level: int):
    for req_lvl, role_id in MILESTONE_ROLES.items():
        role = member.guild.get_role(role_id)
        if not role:
            continue
        if level >= req_lvl and role not in member.roles:
            try:
                await member.add_roles(role, reason=f'Reached Level {req_lvl}')
            except discord.Forbidden:
                pass

@bot.event

async def on_voice_state_update(member, before, after):
    uid = str(member.id)
    # User joined a voice channel
    if before.channel is None and after.channel is not None:
        voice_sessions[uid] = time.time()
    # User left a voice channel
    elif before.channel is not None and after.channel is None:
        if uid in voice_sessions:
            minutes = int((time.time() - voice_sessions[uid]) / 60)
            del voice_sessions[uid]
            data = await get_db(); ensure_user(data, member.id)
            data[uid]["voice_minutes"]        = data[uid].get("voice_minutes", 0) + minutes
            data[uid]["weekly_voice_minutes"] = data[uid].get("weekly_voice_minutes", 0) + minutes
            await save_db(data)

# ─────────────────────────────────────────────
# FRAKTUR NICKNAME SYSTEM
# ─────────────────────────────────────────────
FRAKTUR_MAP = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "𝔬𝔭𝔮𝔯𝔰𝔱𝔲𝔳𝔴𝔵𝔶𝔷𝔸𝔹𝔺𝔻𝔼𝔽𝔾𝔿𝕀𝕁𝕂𝕃𝕄𝕅𝕬𝕭𝕮𝕯𝕰𝕱𝕲𝕳𝕴𝕵𝕶𝕷𝕸𝕹𝕺𝕻𝕼𝕽𝕾𝕿𝖀𝖁𝖂𝖃𝖄𝖅"
)

DONATOR_ROLES = {
    1482893794356494346: '✧',   # Monarch
    1482893965123649566: '✦',   # Overlord
    1482894038502871050: '⚜️',  # Aristocrat
    1482894119092224081: '⚔️',  # Vanguard
}

def to_fraktur(name: str) -> str:
    return name.translate(FRAKTUR_MAP)[:28]

@bot.event
async def on_member_update(before, after):
    if before.roles == after.roles:
        return
    gained = set(after.roles) - set(before.roles)
    lost   = set(before.roles) - set(after.roles)
    for role in gained:
        if role.id in DONATOR_ROLES:
            symbol   = DONATOR_ROLES[role.id]
            new_nick = f'{to_fraktur(after.display_name)} {symbol}'
            try:
                await after.edit(nick=new_nick)
            except discord.Forbidden:
                pass
            return
    for role in lost:
        if role.id in DONATOR_ROLES:
            remaining = [r for r in after.roles if r.id in DONATOR_ROLES]
            if remaining:
                symbol   = DONATOR_ROLES[remaining[0].id]
                new_nick = f'{to_fraktur(after.display_name)} {symbol}'
            else:
                new_nick = None
            try:
                await after.edit(nick=new_nick)
            except discord.Forbidden:
                pass
            return



@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.content:
        return
    snipe_cache[str(message.channel.id)] = {
        "content": message.content,
        "author":  str(message.author),
        "avatar":  str(message.author.display_avatar.url),
        "time":    time.time(),
        "type":    "deleted",
    }

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content:
        return
    snipe_cache[str(before.channel.id)] = {
        "content": before.content,
        "author":  str(before.author),
        "avatar":  str(before.author.display_avatar.url),
        "time":    time.time(),
        "type":    "edited",
    }

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏱️ Cooldown! Try again in **{error.retry_after:.1f}s**.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing: `{error.param.name}`. Use `.help` for usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid argument. Use `.help` for usage.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You don't have permission for that.")
    else:
        raise error

# ─────────────────────────────────────────────
# GENERAL COMMANDS
# ─────────────────────────────────────────────
@bot.command()
async def help(ctx):
    view = HelpView()
    await ctx.send(embed=view.main_embed(), view=view)

@bot.command()
async def credits(ctx):
    data = await get_db(); ensure_user(data, ctx.author.id)
    u = data[str(ctx.author.id)]
    embed = discord.Embed(title=f"🪙 {ctx.author.display_name}'s Credits", color=0xF1C40F)
    embed.add_field(name="Credits", value=f"**{u['credits']:,}**")
    embed.set_footer(text="Earn credits by chatting every 30s (+5 each time)")
    await ctx.send(embed=embed)

@bot.command()
async def charms(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = await get_db(); ensure_user(data, t.id)
    embed = discord.Embed(title=f"✨ {t.display_name}'s Charms", description=f"**{data[str(t.id)]['charms']:,}** charms", color=0xFF69B4)
    await ctx.send(embed=embed)

@bot.command(name="charm")
async def give_charm(ctx, member: discord.Member):
    if member == ctx.author:
        return await ctx.send("❌ You can't charm yourself.")
    data = await get_db()
    ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    uid = str(ctx.author.id)
    data[str(member.id)]["charms"] += 1
    bonus_msg = ""
    if data[uid]["level"] >= 70:
        data[uid]["credits"] += 5
        bonus_msg = " *(+5 credits for your Lvl 70 perk!)*"
    await save_db(data)
    await ctx.send(f"✨ {ctx.author.mention} gave a charm to **{member.display_name}**! They now have **{data[str(member.id)]['charms']}** charms.{bonus_msg}")

@bot.command(aliases=["lb"])
async def leaderboard(ctx, category: str = "economy", page: int = 1):
    data = await get_db()
    page = max(1, page); per = 10; offset = (page - 1) * per
    categories = {
        "economy":   ("💰 Economy",    lambda x: x[1].get("wallet",0)+x[1].get("bank",0), lambda v: f"${v:,}"),
        "leveling":  ("📊 Leveling",   lambda x: (x[1].get("level",1), x[1].get("xp",0)), lambda v: f"Lvl {v[0]} ({v[1]} XP)"),
        "charm":     ("✨ Charms",     lambda x: x[1].get("charms",0),                     lambda v: f"{v:,} charms"),
        "lootcrate": ("📦 Loot Crates",lambda x: x[1].get("lootcrates",0),                lambda v: f"{v:,} crates"),
        "blacktea":  ("🍵 Blacktea",   lambda x: x[1].get("blacktea_wins",0),              lambda v: f"{v:,} wins"),
    }
    cat = category.lower()
    if cat not in categories:
        return await ctx.send(f"❌ Invalid category. Choose: {', '.join(categories)}")
    title, key_fn, fmt_fn = categories[cat]
    sorted_list = sorted(data.items(), key=key_fn, reverse=True)
    total_pages = max(1, (len(sorted_list) + per - 1) // per)
    page = min(page, total_pages)
    slice_ = sorted_list[offset:offset+per]
    embed = discord.Embed(title=f"{title} Leaderboard", color=0xFFD700)
    medals = {1:"🥇", 2:"🥈", 3:"🥉"}
    lines = []
    for i, (uid, u) in enumerate(slice_, start=offset+1):
        badge = medals.get(i, f"**{i}.**")
        val   = key_fn(("", u))
        lines.append(f"{badge} <@{uid}> — {fmt_fn(val)}")
    embed.description = "\n".join(lines) if lines else "No data yet."
    embed.set_footer(text=f"Page {page}/{total_pages}")
    await ctx.send(embed=embed)

@bot.command()
async def boosters(ctx):
    data = await get_db(); ensure_user(data, ctx.author.id)
    u = data[str(ctx.author.id)]
    embed = discord.Embed(title=f"🚀 {ctx.author.display_name}'s Boosters", color=0x9B59B6)
    rem = u.get("booster_end", 0) - time.time()
    if rem > 0:
        embed.add_field(name="2x Booster", value=f"Expires in **{str(datetime.timedelta(seconds=int(rem)))}**")
    else:
        embed.description = "You have no active boosters."
    await ctx.send(embed=embed)

@bot.command()
async def rank(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = await get_db(); ensure_user(data, t.id); u = data[str(t.id)]
    sorted_users = sorted(data.items(), key=lambda x: (x[1].get("prestige",0), x[1].get("level",1), x[1].get("xp",0)), reverse=True)
    pos    = next((i+1 for i,(uid,_) in enumerate(sorted_users) if uid == str(t.id)), "?")
    needed = u["level"] * 500
    filled = int((u["xp"] / needed) * 20) if needed else 0
    bar    = "█" * filled + "░" * (20 - filled)
    embed  = discord.Embed(title=f"📊 {t.display_name}'s Rank", color=0x3498DB)
    embed.set_thumbnail(url=t.display_avatar.url)
    embed.add_field(name="Rank",     value=f"**#{pos}**",                inline=True)
    embed.add_field(name="Level",    value=f"**{u['level']}**",          inline=True)
    embed.add_field(name="Prestige", value=f"**{u.get('prestige',0)}**", inline=True)
    embed.add_field(name="XP",       value=f"`{bar}` {u['xp']}/{needed}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def prestige(ctx):
    data = await get_db(); ensure_user(data, ctx.author.id)
    uid = str(ctx.author.id); u = data[uid]
    if u["level"] < 50:
        return await ctx.send(f"❌ Reach **Level 50** to prestige. You are Level {u['level']}.")
    u["prestige"] = u.get("prestige",0) + 1
    u["level"] = 1; u["xp"] = 0
    await save_db(data)
    await ctx.send(f"🌟 {ctx.author.mention} prestiged! Now **Prestige {u['prestige']}** — Level reset to 1.")

@bot.command()
async def messages(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = await get_db(); ensure_user(data, t.id)
    msg_data = data[str(t.id)].get("messages", {})
    if not msg_data:
        return await ctx.send(f"📭 No message data for **{t.display_name}** yet.")
    embed = discord.Embed(title=f"💬 {t.display_name}'s Messages", color=0x2ECC71)
    total = sum(msg_data.values())
    top   = sorted(msg_data.items(), key=lambda x: x[1], reverse=True)[:10]
    embed.description = "\n".join(f"<#{cid}> — **{cnt:,}**" for cid, cnt in top)
    embed.set_footer(text=f"Total: {total:,} messages")
    await ctx.send(embed=embed)

@bot.command()
async def snipe(ctx):
    cached = snipe_cache.get(str(ctx.channel.id))
    if not cached:
        return await ctx.send("🔍 Nothing to snipe in this channel.")
    age = int(time.time() - cached["time"])
    embed = discord.Embed(description=cached["content"], color=0xE74C3C, timestamp=datetime.datetime.utcnow())
    embed.set_author(name=cached["author"], icon_url=cached["avatar"])
    embed.set_footer(text=f"{'Deleted' if cached['type']=='deleted' else 'Edited'} • {age}s ago")
    await ctx.send(embed=embed)

@bot.command()
async def afk(ctx, *, reason: str = None):
    data = await get_db(); ensure_user(data, ctx.author.id)
    uid = str(ctx.author.id)
    data[uid]["afk"] = reason or "AFK"
    afk_cache[uid]   = reason or "AFK"
    await save_db(data)
    await ctx.send(f"💤 **{ctx.author.display_name}** is now AFK" + (f": *{reason}*" if reason else "."))

@bot.command()
async def partner(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = await get_db(); ensure_user(data, t.id); u = data[str(t.id)]
    embed = discord.Embed(title=f"💍 {t.display_name}'s Marriage", color=0xFF69B4)
    if u.get("partner"):
        date = datetime.datetime.fromtimestamp(u["marry_date"]).strftime("%B %d, %Y")
        days = int((time.time() - u["marry_date"]) / 86400)
        embed.add_field(name="Partner",  value=f"<@{u['partner']}>")
        embed.add_field(name="Married",  value=date)
        embed.add_field(name="Together", value=f"{days} day(s)")
    else:
        embed.description = f"{'You are' if t==ctx.author else f'{t.display_name} is'} currently single 💔"
    await ctx.send(embed=embed)

@bot.command()
async def marry(ctx, member: discord.Member):
    if member == ctx.author or member.bot:
        return await ctx.send("❌ Invalid partner.")
    data = await get_db()
    ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    if data[str(ctx.author.id)]["partner"]:
        return await ctx.send("❌ You're already married. Use `.divorce` first.")
    if data[str(member.id)]["partner"]:
        return await ctx.send(f"❌ {member.display_name} is already married.")

    class ProposeView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="💍 Accept", style=discord.ButtonStyle.success)
        async def yes(self, interaction, btn):
            if interaction.user != member:
                return await interaction.response.send_message("Not your proposal!", ephemeral=True)
            d = await get_db(); now = time.time()
            d[str(ctx.author.id)]["partner"]    = member.id
            d[str(member.id)]["partner"]        = ctx.author.id
            d[str(ctx.author.id)]["marry_date"] = now
            d[str(member.id)]["marry_date"]     = now
            await save_db(d)
            await interaction.response.edit_message(content=f"💍 **{ctx.author.display_name}** and **{member.display_name}** are now married! 🎊", view=None)

        @discord.ui.button(label="💔 Decline", style=discord.ButtonStyle.danger)
        async def no(self, interaction, btn):
            if interaction.user != member:
                return await interaction.response.send_message("Not your proposal!", ephemeral=True)
            await interaction.response.edit_message(content="💔 The proposal was declined.", view=None)

    await ctx.send(f"💍 {member.mention}, **{ctx.author.display_name}** is proposing to you!", view=ProposeView())

@bot.command()
async def divorce(ctx):
    data = await get_db(); ensure_user(data, ctx.author.id)
    uid = str(ctx.author.id)
    pid = data[uid].get("partner")
    if not pid:
        return await ctx.send("❌ You're not married.")
    data[uid]["partner"] = None; data[uid]["marry_date"] = 0
    if str(pid) in data:
        data[str(pid)]["partner"] = None; data[str(pid)]["marry_date"] = 0
    await save_db(data)
    await ctx.send("💔 You are now divorced.")

# ─────────────────────────────────────────────
# LEVEL COMMAND
# ─────────────────────────────────────────────
class LevelView(discord.ui.View):
    def __init__(self, ctx, target, data):
        super().__init__(timeout=120)
        self.ctx = ctx; self.target = target; self.data = data
        self.notif_server = False; self.notif_dm = False

    def progress_embed(self):
        u = self.data[str(self.target.id)]
        lvl = u["level"]; xp = u["xp"]
        needed = lvl * 500
        filled = int((xp / needed) * 20) if needed else 0
        bar    = "█" * filled + "░" * (20 - filled)
        mult   = get_multiplier(self.data, self.target.id)
        prestige = u.get("prestige", 0)
        sorted_users = sorted(self.data.items(), key=lambda x: (x[1].get("prestige",0), x[1].get("level",1), x[1].get("xp",0)), reverse=True)
        rank = next((i+1 for i,(uid,_) in enumerate(sorted_users) if uid == str(self.target.id)), "?")
        milestones = [
            (5,   "📹 Streaming / Camera"),
            (10,  "🖼️ Media Channel Posting"),
            (20,  "😄 External Emojis"),
            (30,  "🎞️ GIFs"),
            (40,  "🎨 Color Panel"),
            (50,  "🗂️ External Stickers"),
            (60,  "📸 Post Images Anywhere"),
            (70,  "✨ +5 Credits per Charm"),
            (80,  "🔊 Soundboards & Voice Messages"),
            (90,  "🎵 External Sounds"),
            (100, "⭐ Prestige"),
        ]
        lines = [f"{'✅' if lvl >= r else '❌'} {n} *(Lvl {r})*" for r, n in milestones]
        embed = discord.Embed(title=f"📊 {self.target.display_name}'s Progress", color=0x5865F2)
        embed.set_thumbnail(url=self.target.display_avatar.url)
        embed.add_field(name="⚡ Level & XP", value=(
            f"**Level:** {lvl}  •  **Prestige:** {prestige}\n"
            f"**XP Multiplier:** {mult}x\n"
            f"**Global Rank:** #{rank}\n"
            f"**Next Level:** {needed - xp:,} XP needed\n"
            f"`{bar}` {xp}/{needed}"
        ), inline=False)
        embed.add_field(name="🏆 Milestones", value="\n".join(lines), inline=False)
        embed.set_footer(text="💡 Earn XP by chatting in text channels or being active in VC!")
        return embed

    def credits_embed(self):
        u = self.data[str(self.target.id)]
        embed = discord.Embed(title=f"🪙 {self.target.display_name}'s Credits", color=0xF1C40F)
        embed.add_field(name="Credits", value=f"**{u['credits']:,}**")
        embed.set_footer(text="Earn credits by chatting every 30s (+5 each time)")
        return embed

    def boosters_embed(self):
        u = self.data[str(self.target.id)]
        rem = u.get("booster_end", 0) - time.time()
        embed = discord.Embed(title=f"🚀 {self.target.display_name}'s Boosters", color=0x9B59B6)
        if rem > 0:
            embed.add_field(name="2x Booster", value=f"Expires in **{str(datetime.timedelta(seconds=int(rem)))}**")
        else:
            embed.description = "No active boosters."
        return embed

    @discord.ui.button(label="Progress", style=discord.ButtonStyle.primary, emoji="📊")
    async def progress_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        await interaction.response.edit_message(embed=self.progress_embed(), view=self)

    @discord.ui.button(label="Credits", style=discord.ButtonStyle.secondary, emoji="🪙")
    async def credits_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        await interaction.response.edit_message(embed=self.credits_embed(), view=self)

    @discord.ui.button(label="Boosters", style=discord.ButtonStyle.secondary, emoji="🚀")
    async def boosters_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        await interaction.response.edit_message(embed=self.boosters_embed(), view=self)

    @discord.ui.button(label="Equip Tag", style=discord.ButtonStyle.success, emoji="🏷️")
    async def equip_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        await interaction.response.send_message("🏷️ Go to **Server Settings → Members → Your Profile** to equip your server tag.", ephemeral=True)

    @discord.ui.button(label="🔔 Server Notifs", style=discord.ButtonStyle.secondary, row=1)
    async def server_notif_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        self.notif_server = not self.notif_server
        btn.style = discord.ButtonStyle.success if self.notif_server else discord.ButtonStyle.secondary
        btn.label = "🔔 Server Notifs ON" if self.notif_server else "🔔 Server Notifs"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="📩 DM Notifs", style=discord.ButtonStyle.secondary, row=1)
    async def dm_notif_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        self.notif_dm = not self.notif_dm
        btn.style = discord.ButtonStyle.success if self.notif_dm else discord.ButtonStyle.secondary
        btn.label = "📩 DM Notifs ON" if self.notif_dm else "📩 DM Notifs"
        await interaction.response.edit_message(view=self)

@bot.command(aliases=["lvl", "level"])
async def progress(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = await get_db(); ensure_user(data, t.id)
    view = LevelView(ctx, t, data)
    await ctx.send(embed=view.progress_embed(), view=view)

# ─────────────────────────────────────────────
# BLACKTEA GAME (NEW VERSION)
# ─────────────────────────────────────────────
import urllib.request

BLACKTEA_CHANNEL_ID = 1482802842900103311
BLACKTEA_REWARD_PER_WORD = 300
BLACKTEA_LIVES = 2
BLACKTEA_TIME_LIMIT = 25  # seconds per turn
BLACKTEA_REACTION_TIME = 30  # seconds to react and join

# Letter combinations for challenges
BLACKTEA_COMBOS = [
    "OS", "AT", "IN", "ER", "AN", "EN", "IS", "OR", "AL", "AR",
    "ON", "LE", "ST", "RE", "NT", "LY", "ED", "TH", "CH", "SH",
    "OST", "ATE", "ING", "ANT", "EST", "OUT", "INT", "OWN", "ARD", "ORS",
    "UND", "ORM", "ACK", "ALL", "ELL", "OOD", "OOL", "EAD", "EAR", "ONG",
]

# Simple English word validation using a word list
ENGLISH_WORDS = set()

async def load_words():
    global ENGLISH_WORDS
    if ENGLISH_WORDS:
        return
    try:
        # Use a common word list
        url = "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as f:
            ENGLISH_WORDS = set(f.read().decode().splitlines())
        print(f"✅ Loaded {len(ENGLISH_WORDS)} English words")
    except Exception as e:
        print(f"⚠️ Could not load word list: {e}")
        # Fallback - accept any word with 3+ letters
        ENGLISH_WORDS = None

def is_valid_word(word: str, combo: str) -> bool:
    word = word.lower().strip()
    combo = combo.lower()
    if len(word) < 3:
        return False
    if combo not in word:
        return False
    if ENGLISH_WORDS is None:
        return True  # fallback: accept if contains combo
    return word in ENGLISH_WORDS

# Active blacktea game state
blacktea_game = None

class BlackteaGame:
    def __init__(self, channel, players):
        self.channel      = channel
        self.players      = list(players)  # list of Member objects
        self.lives        = {p.id: BLACKTEA_LIVES for p in players}
        self.scores       = {p.id: 0 for p in players}
        self.money        = {p.id: 0 for p in players}
        self.used_words   = set()
        self.current_idx  = 0
        self.combo        = ""
        self.active       = True
        self.round        = 0

    @property
    def current_player(self):
        return self.players[self.current_idx % len(self.players)]

    def next_player(self):
        self.current_idx = (self.current_idx + 1) % len(self.players)
        # Skip eliminated players
        checked = 0
        while self.lives[self.current_player.id] <= 0 and checked < len(self.players):
            self.current_idx = (self.current_idx + 1) % len(self.players)
            checked += 1

    def alive_players(self):
        return [p for p in self.players if self.lives[p.id] > 0]

    def new_combo(self):
        import random
        self.combo = random.choice(BLACKTEA_COMBOS)
        self.round += 1

    def results_embed(self):
        embed = discord.Embed(
            title="🍵 Blacktea — Game Over!",
            color=0x8B4513
        )
        # Sort by score
        sorted_players = sorted(self.players, key=lambda p: self.scores[p.id], reverse=True)
        lines = []
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        for i, p in enumerate(sorted_players):
            badge  = medals.get(i, f"**{i+1}.**")
            words  = self.scores[p.id]
            earned = self.money[p.id]
            lives  = self.lives[p.id]
            status = "💀 Eliminated" if lives <= 0 else "🏆 Winner"
            lines.append(f"{badge} **{p.display_name}** — {words} words • ${earned:,} earned • {status}")
        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Total rounds played: {self.round}")
        return embed


async def run_blacktea_game(channel, players):
    global blacktea_game
    game = BlackteaGame(channel, players)
    blacktea_game = game

    await channel.send(
        f"🍵 **Blacktea starts now!** Players: {', '.join(p.mention for p in players)}\n"
        f"Rules: Type a word containing the given letters. Wrong word = hidden countdown. "
        f"Each player has **{BLACKTEA_LIVES} lives** and earns **${BLACKTEA_REWARD_PER_WORD}** per correct word!"
    )
    await asyncio.sleep(3)

    while len(game.alive_players()) >= 2 and game.active:
        player = game.current_player
        if game.lives[player.id] <= 0:
            game.next_player()
            continue

        game.new_combo()
        hearts = "❤️" * game.lives[player.id]

        prompt = await channel.send(
            f"🍵 {player.mention} — Give me a word containing **`{game.combo}`**!\n"
            f"{hearts} | Round {game.round}"
        )

        def check(m):
            return m.author == player and m.channel == channel and not m.bot

        answered_correctly = False
        try:
            while True:
                msg = await bot.wait_for("message", timeout=BLACKTEA_TIME_LIMIT, check=check)
                word = msg.content.lower().strip()

                if word in game.used_words:
                    await channel.send(f"❌ **{word}** was already used! Try another.", delete_after=5)
                    continue

                if is_valid_word(word, game.combo):
                    game.used_words.add(word)
                    game.scores[player.id] += 1
                    game.money[player.id]  += BLACKTEA_REWARD_PER_WORD
                    await channel.send(f"✅ **{word}** accepted! +${BLACKTEA_REWARD_PER_WORD:,}", delete_after=5)
                    answered_correctly = True
                    break
                else:
                    # Wrong word — hidden countdown starts, wait for correct answer
                    # Give 10 more seconds silently
                    try:
                        msg2 = await bot.wait_for("message", timeout=10, check=check)
                        word2 = msg2.content.lower().strip()
                        if word2 in game.used_words:
                            await channel.send(f"❌ Already used!", delete_after=3)
                            raise asyncio.TimeoutError()
                        if is_valid_word(word2, game.combo):
                            game.used_words.add(word2)
                            game.scores[player.id] += 1
                            game.money[player.id]  += BLACKTEA_REWARD_PER_WORD
                            await channel.send(f"✅ **{word2}** accepted! +${BLACKTEA_REWARD_PER_WORD:,}", delete_after=5)
                            answered_correctly = True
                            break
                        else:
                            raise asyncio.TimeoutError()
                    except asyncio.TimeoutError:
                        break

        except asyncio.TimeoutError:
            pass

        if not answered_correctly:
            game.lives[player.id] -= 1
            if game.lives[player.id] <= 0:
                await channel.send(f"💀 **{player.display_name}** has been eliminated! They earned **${game.money[player.id]:,}**.")
                # Pay them out
                data = await get_db()
                ensure_user(data, player.id)
                data[str(player.id)]["wallet"] += game.money[player.id]
                data[str(player.id)]["blacktea_wins"] += game.scores[player.id]
                await save_db(data)
            else:
                hearts_left = "❤️" * game.lives[player.id]
                await channel.send(f"💔 **{player.display_name}** lost a life! {hearts_left} remaining.", delete_after=8)

        game.next_player()
        await asyncio.sleep(2)

    # Game over — pay out remaining players
    game.active = False
    alive = game.alive_players()
    if alive:
        data = await get_db()
        for p in alive:
            ensure_user(data, p.id)
            data[str(p.id)]["wallet"] += game.money[p.id]
            data[str(p.id)]["blacktea_wins"] += game.scores[p.id]
        await save_db(data)

    await channel.send(embed=game.results_embed())
    blacktea_game = None


@tasks.loop(minutes=144)  # ~10 times per 24h (every 2h 24min)
async def blacktea_scheduler():
    import random
    # Random delay so it doesn't always happen at exact same time
    await asyncio.sleep(random.randint(0, 3600))
    channel = bot.get_channel(BLACKTEA_CHANNEL_ID)
    if not channel or blacktea_game:
        return

    # Post join message
    join_msg = await channel.send(
        "🍵 **Blacktea is starting!**\n"
        "React with ✅ in the next **30 seconds** to join the game!\n"
        f"Minimum **2 players** needed. Earn **${BLACKTEA_REWARD_PER_WORD}** per correct word!"
    )
    await join_msg.add_reaction("✅")
    await asyncio.sleep(BLACKTEA_REACTION_TIME)

    # Fetch who reacted
    join_msg = await channel.fetch_message(join_msg.id)
    players  = []
    for reaction in join_msg.reactions:
        if str(reaction.emoji) == "✅":
            async for user in reaction.users():
                if not user.bot:
                    member = channel.guild.get_member(user.id)
                    if member:
                        players.append(member)

    if len(players) < 2:
        await channel.send("🍵 Not enough players joined. Blacktea cancelled.", delete_after=10)
        return

    import random
    random.shuffle(players)
    await run_blacktea_game(channel, players)


@bot.command(name="blacktea")
@commands.has_permissions(administrator=True)
async def blacktea_manual(ctx):
    """Admin: Manually trigger a Blacktea game."""
    channel = bot.get_channel(BLACKTEA_CHANNEL_ID)
    if not channel:
        return await ctx.send("❌ Blacktea channel not found.")
    if blacktea_game:
        return await ctx.send("⚠️ A Blacktea game is already running.")

    join_msg = await channel.send(
        "🍵 **Blacktea is starting!**\n"
        "React with ✅ in the next **30 seconds** to join!\n"
        f"Minimum **2 players** needed. Earn **${BLACKTEA_REWARD_PER_WORD}** per correct word!"
    )
    await join_msg.add_reaction("✅")
    await ctx.send("✅ Blacktea triggered!", delete_after=5)
    await asyncio.sleep(BLACKTEA_REACTION_TIME)

    join_msg = await channel.fetch_message(join_msg.id)
    players  = []
    for reaction in join_msg.reactions:
        if str(reaction.emoji) == "✅":
            async for user in reaction.users():
                if not user.bot:
                    member = channel.guild.get_member(user.id)
                    if member:
                        players.append(member)

    if len(players) < 2:
        await channel.send("🍵 Not enough players joined. Blacktea cancelled.", delete_after=10)
        return

    import random
    random.shuffle(players)
    await run_blacktea_game(channel, players)


# ─────────────────────────────────────────────
# ECONOMY COMMANDS
# ─────────────────────────────────────────────
@bot.command(aliases=["bal"])
async def balance(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = await get_db(); ensure_user(data, t.id); u = data[str(t.id)]
    embed = discord.Embed(title=f"💸 {t.display_name}'s Balance", color=0x2ECC71)
    embed.set_thumbnail(url=t.display_avatar.url)
    embed.add_field(name="👛 Wallet", value=f"${u['wallet']:,}",           inline=True)
    embed.add_field(name="🏦 Bank",   value=f"${u['bank']:,}",             inline=True)
    embed.add_field(name="💰 Total",  value=f"${u['wallet']+u['bank']:,}", inline=True)
    await ctx.send(embed=embed)

@bot.command(aliases=["dep"])
async def deposit(ctx, amount: str):
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    val = parse_amount(amount, data[uid]["wallet"])
    if not val or val <= 0 or val > data[uid]["wallet"]:
        return await ctx.send("❌ Invalid amount or not enough in wallet.")
    data[uid]["wallet"] -= val; data[uid]["bank"] += val
    await save_db(data); await ctx.send(f"✅ Deposited **${val:,}** to your bank.")

@bot.command(aliases=["with"])
async def withdraw(ctx, amount: str):
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    val = parse_amount(amount, data[uid]["bank"])
    if not val or val <= 0 or val > data[uid]["bank"]:
        return await ctx.send("❌ Invalid amount or not enough in bank.")
    data[uid]["bank"] -= val; data[uid]["wallet"] += val
    await save_db(data); await ctx.send(f"🏧 Withdrew **${val:,}** to your wallet.")

@bot.command(aliases=["pay"])
async def give(ctx, member: discord.Member, amount: str):
    if member == ctx.author or member.bot:
        return await ctx.send("❌ Invalid recipient.")
    data = await get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    val = parse_amount(amount, data[str(ctx.author.id)]["wallet"])
    if not val or val <= 0 or val > data[str(ctx.author.id)]["wallet"]:
        return await ctx.send("❌ Invalid amount.")
    data[str(ctx.author.id)]["wallet"] -= val
    data[str(member.id)]["wallet"]     += val
    await save_db(data); await ctx.send(f"💸 **{ctx.author.display_name}** gave **${val:,}** to **{member.display_name}**.")

@bot.command()
@commands.cooldown(1, 36, commands.BucketType.user)
async def work(ctx):
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    jobs = ["programmer","chef","taxi driver","streamer","delivery driver","barista","teacher","nurse"]
    pay  = random.randint(500, 1500) * get_multiplier(data, ctx.author.id)
    data[uid]["wallet"] += pay; data[uid]["last_work"] = time.time()
    await save_db(data); await ctx.send(f"💼 You worked as a **{random.choice(jobs)}** and earned **${pay:,}**!")

@bot.command()
@commands.cooldown(1, 86400, commands.BucketType.user)
async def daily(ctx):
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    reward = 2500 * get_multiplier(data, ctx.author.id)
    data[uid]["wallet"] += reward; data[uid]["last_daily"] = time.time()
    await save_db(data); await ctx.send(f"🎁 Daily claimed! **+${reward:,}**")

@bot.command()
@commands.cooldown(1, 604800, commands.BucketType.user)
async def weekly(ctx):
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    reward = 15000 * get_multiplier(data, ctx.author.id)
    data[uid]["wallet"] += reward; data[uid]["last_weekly"] = time.time()
    await save_db(data); await ctx.send(f"🎁 Weekly claimed! **+${reward:,}**")

@bot.command()
async def cooldowns(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = await get_db(); ensure_user(data, t.id); u = data[str(t.id)]
    now = time.time()
    def fmt(r): return "✅ Ready" if r <= 0 else f"⏱️ {str(datetime.timedelta(seconds=int(r)))}"
    embed = discord.Embed(title=f"⏱️ {t.display_name}'s Cooldowns", color=0xE67E22)
    embed.add_field(name="💼 Work",   value=fmt(36     - (now - u.get("last_work",  0))), inline=True)
    embed.add_field(name="🎁 Daily",  value=fmt(86400  - (now - u.get("last_daily", 0))), inline=True)
    embed.add_field(name="🎁 Weekly", value=fmt(604800 - (now - u.get("last_weekly",0))), inline=True)
    embed.add_field(name="🥷 Rob",    value=fmt(7200   - (now - u.get("last_rob",   0))), inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def inbox(ctx, page: int = 1):
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    msgs = data[uid].get("inbox", [])
    if not msgs: return await ctx.send("📭 Your inbox is empty.")
    per = 5; page = max(1, min(page, max(1,(len(msgs)+per-1)//per)))
    slice_ = msgs[(page-1)*per:page*per]
    embed = discord.Embed(title="📬 Your Inbox", color=0x3498DB)
    for i, m in enumerate(slice_, start=(page-1)*per+1):
        embed.add_field(name=f"#{i} — {m.get('from','System')}", value=m.get("text","…"), inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def rob(ctx, member: discord.Member):
    if member == ctx.author: return await ctx.send("❌ You can't rob yourself.")
    data = await get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    uid = str(ctx.author.id); tid = str(member.id)
    cd_left = 7200 - (time.time() - data[uid].get("last_rob", 0))
    if cd_left > 0: return await ctx.send(f"⏱️ Rob cooldown! **{str(datetime.timedelta(seconds=int(cd_left)))}** remaining.")
    if data[tid]["wallet"] < 500: return await ctx.send("❌ They don't have enough (need $500+).")
    data[uid]["last_rob"] = time.time()
    if random.randint(1, 100) <= 45:
        stolen = random.randint(100, max(100, int(data[tid]["wallet"] * 0.3)))
        data[uid]["wallet"] += stolen; data[tid]["wallet"] -= stolen
        await ctx.send(f"🥷 Success! Stole **${stolen:,}** from {member.display_name}.")
    else:
        fine = min(1000, data[uid]["wallet"])
        data[uid]["wallet"] = max(0, data[uid]["wallet"] - fine)
        await ctx.send(f"🚓 Busted! Paid a **${fine:,}** fine.")
    await save_db(data)

# ─────────────────────────────────────────────
# GAMBLING
# ─────────────────────────────────────────────
@bot.command(aliases=["cf"])
async def coinflip(ctx, amount: str, side: str):
    side = side.lower()
    if side not in ("heads","tails","h","t"): return await ctx.send("❌ Choose `heads` or `tails`.")
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    bet = parse_amount(amount, data[uid]["wallet"])
    if not bet or bet <= 0 or bet > data[uid]["wallet"]: return await ctx.send("❌ Invalid bet.")
    data[uid]["wallet"] -= bet
    result = random.choice(["heads","tails"])
    if side[0] == result[0]:
        win = bet * get_multiplier(data, ctx.author.id)
        data[uid]["wallet"] += bet + win
        msg = f"🪙 **{result.upper()}!** You won **${win:,}**! 🎉"
    else:
        msg = f"🪙 **{result.upper()}...** You lost **${bet:,}**."
    await save_db(data); await ctx.send(msg)

class BlackjackView(discord.ui.View):
    VALS  = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]
    SUITS = ["♠","♥","♦","♣"]
    def __init__(self, ctx, bet, data):
        super().__init__(timeout=120)
        self.ctx = ctx; self.bet = bet; self.data = data
        deck = [f"{v}{s}" for s in self.SUITS for v in self.VALS] * 2
        random.shuffle(deck); self.deck = deck
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
    def val(self, card):
        v = card[:-1]
        if v in ("J","Q","K"): return 10
        if v == "A": return 11
        return int(v)
    def total(self, hand):
        t = sum(self.val(c) for c in hand)
        aces = sum(1 for c in hand if c[:-1]=="A")
        while t > 21 and aces: t -= 10; aces -= 1
        return t
    def build_embed(self, ended=False, note=""):
        pt = self.total(self.player); dt = self.total(self.dealer)
        e  = discord.Embed(title="🃏 Blackjack", color=0x2ECC71)
        e.add_field(name=f"Dealer {'('+str(dt)+')' if ended else ''}", value="  ".join(self.dealer) if ended else f"{self.dealer[0]}  🂠", inline=False)
        e.add_field(name=f"You ({pt})", value="  ".join(self.player), inline=False)
        e.add_field(name="Bet", value=f"${self.bet:,}", inline=True)
        if note: e.set_footer(text=note)
        return e
    async def resolve(self, interaction, status):
        uid = str(self.ctx.author.id); mult = get_multiplier(self.data, self.ctx.author.id)
        if status == "win":
            win = self.bet * mult; self.data[uid]["wallet"] += self.bet + win; note = f"🏆 You win! +${win:,}"
        elif status == "blackjack":
            win = int(self.bet * 1.5); self.data[uid]["wallet"] += self.bet + win; note = f"🃏 Blackjack! +${win:,}"
        elif status == "push":
            self.data[uid]["wallet"] += self.bet; note = "🤝 Push — bet returned."
        else:
            note = f"😔 You lost ${self.bet:,}."
        await save_db(self.data)
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(embed=self.build_embed(ended=True, note=note), view=self)
    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction, btn):
        if interaction.user != self.ctx.author: return await interaction.response.send_message("Not your game!", ephemeral=True)
        self.player.append(self.deck.pop()); pt = self.total(self.player)
        if pt > 21: return await self.resolve(interaction, "lose")
        if pt == 21: return await self.stand_logic(interaction)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction, btn):
        if interaction.user != self.ctx.author: return await interaction.response.send_message("Not your game!", ephemeral=True)
        await self.stand_logic(interaction)
    async def stand_logic(self, interaction):
        while self.total(self.dealer) < 17: self.dealer.append(self.deck.pop())
        p, d = self.total(self.player), self.total(self.dealer)
        status = "win" if (d > 21 or p > d) else ("push" if p == d else "lose")
        await self.resolve(interaction, status)

@bot.command(aliases=["bj"])
async def blackjack(ctx, amount: str):
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    bet = parse_amount(amount, data[uid]["wallet"])
    if not bet or bet <= 0 or bet > data[uid]["wallet"]: return await ctx.send("❌ Invalid bet.")
    data[uid]["wallet"] -= bet; await save_db(data)
    view = BlackjackView(ctx, bet, data)
    if view.total(view.player) == 21:
        win = int(bet * 1.5); data[uid]["wallet"] += bet + win; await save_db(data)
        return await ctx.send(embed=view.build_embed(ended=True, note=f"🃏 Blackjack! +${win:,}"))
    await ctx.send(embed=view.build_embed(), view=view)

_REDS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
ROULETTE_BETS = {
    "red":    (lambda n: n in _REDS, 2),
    "black":  (lambda n: n != 0 and n not in _REDS, 2),
    "even":   (lambda n: n != 0 and n%2==0, 2),
    "odd":    (lambda n: n%2==1, 2),
    "low":    (lambda n: 1<=n<=18, 2),
    "high":   (lambda n: 19<=n<=36, 2),
    "dozen1": (lambda n: 1<=n<=12, 3),
    "dozen2": (lambda n: 13<=n<=24, 3),
    "dozen3": (lambda n: 25<=n<=36, 3),
}

@bot.command()
async def roulette(ctx, amount: str, bet_type: str):
    bt = bet_type.lower()
    if bt not in ROULETTE_BETS: return await ctx.send(f"❌ Types: {', '.join(f'`{k}`' for k in ROULETTE_BETS)}")
    data = await get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    bet = parse_amount(amount, data[uid]["wallet"])
    if not bet or bet <= 0 or bet > data[uid]["wallet"]: return await ctx.send("❌ Invalid bet.")
    data[uid]["wallet"] -= bet
    spin = random.randint(0, 36)
    icon = "🔴" if spin in _REDS else ("🟩" if spin == 0 else "⚫")
    check_fn, mult = ROULETTE_BETS[bt]
    embed = discord.Embed(title="🎡 Roulette", color=0xC0392B)
    embed.add_field(name="Spin", value=f"{icon} **{spin}**", inline=True)
    embed.add_field(name="Your Bet", value=f"`{bt}` — ${bet:,}", inline=True)
    if check_fn(spin):
        win = bet * (mult - 1) * get_multiplier(data, ctx.author.id)
        data[uid]["wallet"] += bet + win
        embed.add_field(name="Result", value=f"🎉 Win! **+${win:,}**", inline=False)
        embed.color = 0x2ECC71
    else:
        embed.add_field(name="Result", value=f"😔 Lost **${bet:,}**.", inline=False)
    await save_db(data); await ctx.send(embed=embed)

class RPSView(discord.ui.View):
    def __init__(self, ctx, opponent, bet, data):
        super().__init__(timeout=60)
        self.ctx = ctx; self.challenger = ctx.author; self.opponent = opponent
        self.bet = bet; self.data = data
        self.choices = {ctx.author.id: None, opponent.id: None}
    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction, btn):
        if interaction.user != self.opponent: return await interaction.response.send_message("Not your challenge!", ephemeral=True)
        self.clear_items()
        for c in ["Rock","Paper","Scissors"]:
            b = discord.ui.Button(label=c, custom_id=c.lower()); b.callback = self.pick; self.add_item(b)
        await interaction.response.edit_message(content="⚔️ Both players pick your move!", view=self)
    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, btn):
        if interaction.user != self.opponent: return await interaction.response.send_message("Not your challenge!", ephemeral=True)
        await interaction.response.edit_message(content="❌ Duel declined.", view=None)
    async def pick(self, interaction):
        if interaction.user.id not in self.choices: return
        if self.choices[interaction.user.id]: return await interaction.response.send_message("Already picked!", ephemeral=True)
        self.choices[interaction.user.id] = interaction.data["custom_id"]
        await interaction.response.send_message(f"You picked **{interaction.data['custom_id']}**!", ephemeral=True)
        if all(v for v in self.choices.values()):
            beats = {"rock":"scissors","paper":"rock","scissors":"paper"}
            c, o = self.choices[self.challenger.id], self.choices[self.opponent.id]
            if c == o: result = "🤝 Draw! Bets returned."
            elif beats[c] == o:
                self.data[str(self.challenger.id)]["wallet"] += self.bet
                self.data[str(self.opponent.id)]["wallet"]   -= self.bet
                result = f"🏆 **{self.challenger.display_name}** wins **${self.bet:,}**!"
            else:
                self.data[str(self.opponent.id)]["wallet"]   += self.bet
                self.data[str(self.challenger.id)]["wallet"] -= self.bet
                result = f"🏆 **{self.opponent.display_name}** wins **${self.bet:,}**!"
            await save_db(self.data)
            await interaction.message.edit(content=result, view=None)

@bot.command()
async def rps(ctx, member: discord.Member, amount: str):
    data = await get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    bet = parse_amount(amount, data[str(ctx.author.id)]["wallet"])
    if not bet or bet <= 0: return await ctx.send("❌ Invalid bet.")
    if bet > data[str(ctx.author.id)]["wallet"]: return await ctx.send(f"❌ {ctx.author.display_name} can't afford that.")
    if bet > data[str(member.id)]["wallet"]:     return await ctx.send(f"❌ {member.display_name} can't afford that.")
    await ctx.send(f"⚔️ {member.mention}, **{ctx.author.display_name}** challenges you to RPS for **${bet:,}**!", view=RPSView(ctx, member, bet, data))

class DuelView(discord.ui.View):
    def __init__(self, ctx, opponent, bet, data):
        super().__init__(timeout=60)
        self.ctx = ctx; self.challenger = ctx.author; self.opponent = opponent
        self.bet = bet; self.data = data
    @discord.ui.button(label="⚔️ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction, btn):
        if interaction.user != self.opponent: return await interaction.response.send_message("Not your duel!", ephemeral=True)
        winner = random.choice([self.challenger, self.opponent])
        loser  = self.opponent if winner == self.challenger else self.challenger
        self.data[str(winner.id)]["wallet"] += self.bet
        self.data[str(loser.id)]["wallet"]  -= self.bet
        await save_db(self.data)
        await interaction.response.edit_message(content=f"⚔️ **{winner.display_name}** wins and takes **${self.bet:,}**!", view=None)
    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, btn):
        if interaction.user != self.opponent: return await interaction.response.send_message("Not your duel!", ephemeral=True)
        await interaction.response.edit_message(content="❌ Duel declined.", view=None)

@bot.command()
async def duel(ctx, member: discord.Member, amount: str):
    data = await get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    bet = parse_amount(amount, data[str(ctx.author.id)]["wallet"])
    if not bet or bet <= 0: return await ctx.send("❌ Invalid bet.")
    if bet > data[str(ctx.author.id)]["wallet"]: return await ctx.send(f"❌ {ctx.author.display_name} can't afford that.")
    if bet > data[str(member.id)]["wallet"]:     return await ctx.send(f"❌ {member.display_name} can't afford that.")
    await ctx.send(f"⚔️ {member.mention}, **{ctx.author.display_name}** challenges you to a duel for **${bet:,}**!", view=DuelView(ctx, member, bet, data))

class TTTView(discord.ui.View):
    def __init__(self, ctx, opponent, bet, data):
        super().__init__(timeout=120)
        self.ctx = ctx; self.challenger = ctx.author; self.opponent = opponent
        self.bet = bet; self.data = data
        self.board = [None] * 9; self.turn = ctx.author; self.accepted = False
        ab = discord.ui.Button(label="✅ Accept", style=discord.ButtonStyle.success, custom_id="ttt_accept")
        ab.callback = self.do_accept; self.add_item(ab)
        db = discord.ui.Button(label="❌ Decline", style=discord.ButtonStyle.danger, custom_id="ttt_decline")
        db.callback = self.do_decline; self.add_item(db)
    async def do_accept(self, interaction):
        if interaction.user != self.opponent: return await interaction.response.send_message("Not your game!", ephemeral=True)
        self.accepted = True; self.clear_items()
        for i in range(9):
            b = discord.ui.Button(label="⬜", style=discord.ButtonStyle.secondary, custom_id=f"ttt_{i}", row=i//3)
            b.callback = self.move; self.add_item(b)
        await interaction.response.edit_message(content=self.status(), view=self)
    async def do_decline(self, interaction):
        if interaction.user != self.opponent: return await interaction.response.send_message("Not your game!", ephemeral=True)
        await interaction.response.edit_message(content="❌ Game declined.", view=None)
    def status(self):
        return f"❌ = {self.challenger.display_name}  |  ⭕ = {self.opponent.display_name}\n🎯 **{self.turn.display_name}'s turn**"
    async def move(self, interaction):
        if not self.accepted: return
        if interaction.user != self.turn: return await interaction.response.send_message("Not your turn!", ephemeral=True)
        idx = int(interaction.data["custom_id"].split("_")[1])
        if self.board[idx]: return
        self.board[idx] = self.turn
        sym = "❌" if self.turn == self.challenger else "⭕"
        for b in self.children:
            if getattr(b, "custom_id", "") == f"ttt_{idx}":
                b.label = sym; b.disabled = True
                b.style = discord.ButtonStyle.primary if sym=="❌" else discord.ButtonStyle.danger
        winner = self.check_winner()
        if winner:
            loser = self.opponent if winner == self.challenger else self.challenger
            self.data[str(winner.id)]["wallet"] += self.bet
            self.data[str(loser.id)]["wallet"]  -= self.bet
            await save_db(self.data)
            for b in self.children: b.disabled = True
            return await interaction.response.edit_message(content=f"🏆 **{winner.display_name}** wins and takes **${self.bet:,}**!", view=self)
        if all(self.board):
            for b in self.children: b.disabled = True
            return await interaction.response.edit_message(content="🤝 Draw! Bets returned.", view=self)
        self.turn = self.opponent if self.turn == self.challenger else self.challenger
        await interaction.response.edit_message(content=self.status(), view=self)
    def check_winner(self):
        for a,b,c in [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]:
            if self.board[a] and self.board[a]==self.board[b]==self.board[c]: return self.board[a]
        return None

@bot.command(aliases=["tictactoe"])
async def ttt(ctx, member: discord.Member, amount: str):
    data = await get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    bet = parse_amount(amount, data[str(ctx.author.id)]["wallet"])
    if not bet or bet <= 0: return await ctx.send("❌ Invalid bet.")
    if bet > data[str(ctx.author.id)]["wallet"]: return await ctx.send(f"❌ {ctx.author.display_name} can't afford that.")
    if bet > data[str(member.id)]["wallet"]:     return await ctx.send(f"❌ {member.display_name} can't afford that.")
    await ctx.send(f"🎮 {member.mention}, **{ctx.author.display_name}** challenges you to Tic-Tac-Toe for **${bet:,}**!", view=TTTView(ctx, member, bet, data))

# ─────────────────────────────────────────────
# FUN COMMANDS
# ─────────────────────────────────────────────
_FUN = [
    ("hug",      discord.Color.from_rgb(255,182,193)),
    ("kiss",     discord.Color.red()),
    ("dance",    discord.Color.purple()),
    ("handhold", discord.Color.from_rgb(255,200,210)),
    ("cry",      discord.Color.blue()),
    ("bite",     discord.Color.dark_red()),
    ("poke",     discord.Color.orange()),
    ("lick",     discord.Color.from_rgb(255,220,100)),
    ("highfive", discord.Color.green()),
    ("slap",     discord.Color.dark_orange()),
    ("cuddle",   discord.Color.from_rgb(255,160,180)),
    ("kill",     discord.Color.dark_gray()),
]
for _action, _color in _FUN:
    def _make(action, color):
        @bot.command(name=action)
        async def _fun_cmd(ctx, member: discord.Member, _a=action, _c=color):
            await send_action(ctx, _a, member, _c)
        _fun_cmd.__name__ = action
    _make(_action, _color)

@bot.command()
async def bestie(ctx, member1: discord.Member, member2: discord.Member = None):
    a = member1; b = member2 or ctx.author
    pct  = abs(hash(f"{min(a.id,b.id)}{max(a.id,b.id)}")) % 101
    bar  = "█"*int(pct/5) + "░"*(20-int(pct/5))
    note = "💕 Absolute besties!" if pct>=80 else ("😊 Pretty good friends!" if pct>=50 else "🤔 Could be better...")
    embed = discord.Embed(title="👯 Bestie Compatibility", description=f"**{a.display_name}** & **{b.display_name}**\n\n`{bar}` **{pct}%**", color=0xFF69B4)
    embed.set_footer(text=note)
    await ctx.send(embed=embed)

@bot.command()
async def aura(ctx, member: discord.Member = None):
    t = member or ctx.author
    power = abs(hash(str(t.id)+"aura")) % 1001
    if power >= 900:   label, color = "✨ Legendary Aura", 0xFFD700
    elif power >= 700: label, color = "🌟 Radiant Aura",  0xFFA500
    elif power >= 400: label, color = "💫 Neutral Aura",  0x3498DB
    else:              label, color = "🌑 Dark Aura",      0x2C3E50
    embed = discord.Embed(title=f"🔮 {t.display_name}'s Aura", description=f"**{label}**\nAura Power: **{power}/1000**", color=color)
    await ctx.send(embed=embed)

@bot.command()
async def ship(ctx, member1: discord.Member, member2: discord.Member = None):
    a = member1; b = member2 or ctx.author
    pct  = abs(hash(f"{min(a.id,b.id)}{max(a.id,b.id)}ship")) % 101
    bar  = "💗"*(pct//10) + "🤍"*(10-pct//10)
    name = a.display_name[:max(1,len(a.display_name)//2)] + b.display_name[len(b.display_name)//2:]
    note = "💍 Soulmates!" if pct>=90 else ("💕 Strong connection!" if pct>=60 else ("🙂 There's potential!" if pct>=30 else "💔 Not meant to be..."))
    embed = discord.Embed(title="💘 Ship Meter", description=f"**{a.display_name}** 💞 **{b.display_name}**\nShip name: **{name}**\n\n`{bar}` **{pct}%**", color=0xFF1493)
    embed.set_footer(text=note)
    await ctx.send(embed=embed)

# ─────────────────────────────────────────────
# SETUP COMMANDS
# ─────────────────────────────────────────────
async def post_guidelines(channel):
    embed = discord.Embed(
        title="📋  Hang Spot — Guidelines",
        description=(
            "Welcome to **Hang Spot** 🍄\n"
            "An **active and chill** community with emotes and daily giveaways.\n"
            "Please read and respect the rules below before participating.\n\n"
            "**Owner:** EN1SSAY\n"
        ),
        color=0xFF85A1
    )
    embed.add_field(name="1️⃣  Discord TOS",      value="Follow [Discord's Terms of Service](https://discord.com/terms) at all times.", inline=False)
    embed.add_field(name="2️⃣  Be Respectful",    value="Hate speech, harassment, sexism, racism, and doxing are **strictly forbidden**.", inline=False)
    embed.add_field(name="3️⃣  SFW Only",         value="This server is **strictly Safe For Work**. No NSFW content. No e-dating.", inline=False)
    embed.add_field(name="4️⃣  No Advertising",   value="Unauthorized promotion or poaching of members is **not allowed**.", inline=False)
    embed.add_field(name="5️⃣  Staff Discretion", value="Staff may take action without prior warning. Follow staff instructions.", inline=False)
    embed.set_footer(text="By participating in this server you agree to these rules.")
    await channel.send(embed=embed, view=GuidelinesView())

async def post_perks(channel):
    embed = discord.Embed(
        description=(
            "✧ ━━━━━━━━━━━━━━━━━━ ✧\n"
            "👑 ﹒ 𝕯𝖔𝖓𝖆𝖙𝖔𝖗 𝕽𝖔𝖑𝖊𝖘 & 𝕻𝖊𝖗𝖐𝖘\n"
            "✧ ━━━━━━━━━━━━━━━━━━ ✧"
        ),
        color=0xFFD700
    )

    embed.add_field(
        name="𝕸𝖔𝖓𝖆𝖗𝖈𝖍 ✧",
        value=(
            "*all perks are lifetime*\n"
            "✦ all subscription perks + roles\n"
            "✦ special 𝕸𝖔𝖓𝖆𝖗𝖈𝖍 icon next to your name\n"
            "✦ trial moderator role (able to be promoted)\n"
            "✦ $15,000,000 currency in economy\n"
            "✦ access to audit logs + punishment immunity\n"
            "✦ ability to join full vcs + secret monarch vc\n"
            "✦ may request custom perks / roles\n"
            "🥀 to gift, buy or claim perks make a ticket"
        ),
        inline=False
    )

    embed.add_field(
        name="𝕺𝖛𝖊𝖗𝖑𝖔𝖗𝖉 ✦",
        value=(
            "*the _ represents a new perk for the role*\n"
            "✦ all obtainable sub perks\n"
            "✦ special 𝕺𝖛𝖊𝖗𝖑𝖔𝖗𝖉 icon next to your name\n"
            "✦ access to special color menu\n"
            "✦ $2,500,000 currency in economy\n"
            "✦ _ custom role (12 users)\n"
            "✦ _ ability to join full vcs\n"
            "✦ _ ability to use avatar / banner cmd\n"
            "✦ _ may request other perks\n"
            "🥀 to gift this role or claim perks make a ticket"
        ),
        inline=False
    )

    embed.add_field(
        name="𝕬𝖗𝖎𝖘𝖙𝖔𝖈𝖗𝖆𝖙 ⚜️",
        value=(
            "*the _ represents a new perk for the role*\n"
            "✦ special 𝕬𝖗𝖎𝖘𝖙𝖔𝖈𝖗𝖆𝖙 icon next to your name\n"
            "✦ access to special color menu\n"
            "✦ send images & gifs in chats\n"
            "✦ send voice messages in chats\n"
            "✦ $500,000 currency in economy\n"
            "✦ _ ability to use snipe command\n"
            "✦ _ custom role (5 users)\n"
            "✦ _ external emoji + sticker perms\n"
            "✦ _ soundboard + external soundboard perms\n"
            "🥀 to gift this role or claim perks make a ticket"
        ),
        inline=False
    )

    embed.add_field(
        name="𝖁𝖆𝖓𝖌𝖚𝖆𝖗𝖉 ⚔️",
        value=(
            "*the _ represents a new perk for the role*\n"
            "✦ special 𝖁𝖆𝖓𝖌𝖚𝖆𝖗𝖉 icon next to your name\n"
            "✦ access to special color menu\n"
            "✦ send images & gifs in chats\n"
            "✦ send voice messages in chats\n"
            "✦ $150,000 currency in economy\n"
            "✦ _ ability to use snipe command\n"
            "🥀 to gift this role or claim perks make a ticket"
        ),
        inline=False
    )

    embed.add_field(
        name="✧ ━━━━━━━━━━━━━━━━━━ ✧\n📊 ﹒ 𝕷𝖊𝖛𝖊𝖑 𝕸𝖎𝖑𝖊𝖘𝖙𝖔𝖓𝖊𝖘",
        value=(
            "**Lvl 5** — 📹 Streaming / Camera\n"
            "**Lvl 10** — 🖼️ Media Channel Posting\n"
            "**Lvl 20** — 😄 External Emojis\n"
            "**Lvl 30** — 🎞️ GIFs\n"
            "**Lvl 40** — 🎨 Color Panel\n"
            "**Lvl 50** — 🗂️ External Stickers\n"
            "**Lvl 60** — 📸 Post Images Anywhere\n"
            "**Lvl 70** — ✨ +5 Credits per charm\n"
            "**Lvl 80** — 🔊 Soundboards & Voice Messages\n"
            "**Lvl 90** — 🎵 External Sounds\n"
            "**Lvl 100** — ⭐ Prestige Unlock"
        ),
        inline=False
    )

    embed.add_field(
        name="✧ ━━━━━━━━━━━━━━━━━━ ✧\n👑 ﹒ 𝖂𝖊𝖊𝖐𝖑𝖞 𝕽𝖔𝖑𝖊𝖘",
        value=(
            "**👸 Princess / 🤴 Prince** — Top chatters of the week\n"
            "Awarded every Monday to the top users in <#1482802842900103311>\n\n"
"**👸 Princess / 🤴 Prince** — Top chatters of the week\n"
            "Awarded every Sunday to the top male/female chatters in <#1482802842900103311>"
        ),
        inline=False
    )

    embed.set_footer(text="✧ ━━━━━━━━━━━━━━━━━━ ✧\nUse .level to track your progress • Make a ticket to donate")
    await channel.send(embed=embed)

async def post_profile(channel):
    gender_embed = discord.Embed(
        title="💮  Gender Roles",
        description="Pick your gender role below.\nClick again to **remove** it.\n\n**1.** 🌸 Female\n**2.** 💙 Male",
        color=0xFF85A1
    )
    await channel.send(embed=gender_embed, view=GenderView())
    color_embed = discord.Embed(
        title="🎨  Color Roles",
        description=(
            "Choose your name color from the dropdown below!\n\n"
            "**Red / Pink**\n🔴 Scarlet Fury  •  🟠 Fire Pop  •  🌸 Rose Dust\n❤️ Crimson Blaze  •  🍇 Raspberry Burst  •  🌷 Blush Bloom\n\n"
            "**Yellow / Orange**\n🟡 Golden Ember  •  🍯 Sunbeam Honey  •  🍑 Apricot Glow\n\n"
            "**Green**\n💚 Emerald Surge  •  🌿 Mint Breeze  •  🩵 Frosted Mist\n\n"
            "**Blue**\n🌊 Ocean Depth"
        ),
        color=0x5865F2
    )
    await channel.send(embed=color_embed, view=ColorView())

async def post_support(channel):
    embed = discord.Embed(
        title="💌  Hang Spot — Support",
        description=(
            "Need help? Click the button below to open a **private support ticket**.\n\n"
            "A staff member will assist you as soon as possible.\n\n"
            "🔒 Your ticket is **private** — only you and staff can see it."
        ),
        color=0xFF85A1
    )
    embed.set_footer(text="Please be patient and respectful with staff.")
    await channel.send(embed=embed, view=TicketView())

@bot.command()
@commands.has_permissions(administrator=True)
async def setupguidelines(ctx):
    await post_guidelines(ctx.channel)
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def setupperks(ctx):
    await post_perks(ctx.channel)
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def setupprofile(ctx):
    await post_profile(ctx.channel)
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def setupsupport(ctx):
    await post_support(ctx.channel)
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def setupall(ctx, guidelines_channel: discord.TextChannel, profile_channel: discord.TextChannel):
    await post_guidelines(guidelines_channel)
    await post_profile(profile_channel)
    await ctx.send(f"✅ Guidelines posted in {guidelines_channel.mention} and profile in {profile_channel.mention}.")


# ─────────────────────────────────────────────
# SHOP SYSTEM
# ─────────────────────────────────────────────

GUARDIAN_ROLE_ID = 1483096681820983508

SHOP_ITEMS = {
    "xp2_4h":   {"name": "2x XP Booster [4h]",      "cost": 5_000,     "emoji": "⚡", "boost": 2, "duration": 4},
    "xp2_10h":  {"name": "2x XP Booster [10h]",     "cost": 10_000,    "emoji": "⚡", "boost": 2, "duration": 10},
    "xp2_24h":  {"name": "2x XP Booster [24h]",     "cost": 20_000,    "emoji": "⚡", "boost": 2, "duration": 24},
    "xp3_24h":  {"name": "3x XP Booster [24h]",     "cost": 50_000,    "emoji": "🔥", "boost": 3, "duration": 24},
    "xp2_life": {"name": "2x XP Booster [Life]",    "cost": 2_000_000, "emoji": "♾️", "boost": 2, "duration": -1},
    "guardian": {"name": "Guardian Role",            "cost": 2_500_000, "emoji": "🗡️", "boost": 0, "duration": 0},
    "bronze":   {"name": "Bronze Lottery Ticket",    "cost": 5_000,     "emoji": "🥉", "boost": 0, "duration": 0},
    "silver":   {"name": "Silver Lottery Ticket",    "cost": 10_000,    "emoji": "🥈", "boost": 0, "duration": 0},
    "gold":     {"name": "Gold Lottery Ticket",      "cost": 50_000,    "emoji": "🏆", "boost": 0, "duration": 0},
}

LOTTERY_PRIZES = {
    "bronze": (10_000,  22_500),
    "silver": (20_000,  45_000),
    "gold":   (100_000, 225_000),
}

class ShopView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx

    def shop_embed(self):
        embed = discord.Embed(
            title="🛒  Hang Spot — Shop",
            description="Spend your economy money on boosters, roles and lottery tickets!\nUse `.buy <item>` to purchase.",
            color=0xFFD700
        )
        embed.add_field(
            name="⚡ XP Boosters",
            value=(
                "`.buy xp2_4h`   — 2x XP Booster [4h]      — $5,000\n"
                "`.buy xp2_10h`  — 2x XP Booster [10h]     — $10,000\n"
                "`.buy xp2_24h`  — 2x XP Booster [24h]     — $20,000\n"
                "`.buy xp3_24h`  — 3x XP Booster [24h]     — $50,000\n"
                "`.buy xp2_life` — 2x XP Booster [♾️ Life] — $2,000,000"
            ),
            inline=False
        )
        embed.add_field(
            name="🗡️ Roles",
            value="`.buy guardian` — G̶u̶a̶r̶d̶i̶a̶n̶˚ Role — $2,500,000",
            inline=False
        )
        embed.add_field(
            name="🎰 Lottery Tickets",
            value=(
                "`.buy bronze` — 🥉 Bronze Ticket — $5,000   (win $10k-$22.5k)\n"
                "`.buy silver` — 🥈 Silver Ticket — $10,000  (win $20k-$45k)\n"
                "`.buy gold`   — 🏆 Gold Ticket   — $50,000  (win $100k-$225k)"
            ),
            inline=False
        )
        embed.set_footer(text="Match 3 of the same on a scratch card to win!")
        return embed


class ScratchView(discord.ui.View):
    def __init__(self, ctx, ticket_type, data):
        super().__init__(timeout=120)
        self.ctx         = ctx
        self.ticket_type = ticket_type
        self.data        = data
        self.revealed    = 0
        self.grid        = self._generate_grid()
        self.done        = False

        for i in range(9):
            btn = discord.ui.Button(
                label="🎴",
                style=discord.ButtonStyle.secondary,
                custom_id=f"scratch_{i}",
                row=i // 3
            )
            btn.callback = self.scratch
            self.add_item(btn)

    def _generate_grid(self):
        min_prize, max_prize = LOTTERY_PRIZES[self.ticket_type]
        base_values = [
            min_prize,
            int(min_prize * 1.5),
            int(min_prize * 2),
            int(min_prize * 2.5),
            max_prize,
        ]
        grid = random.choices(base_values, k=9)
        if random.random() < 0.35:
            win_val   = random.choice(base_values)
            positions = random.sample(range(9), 3)
            for p in positions:
                grid[p] = win_val
        return grid

    def _check_winner(self):
        from collections import Counter
        counts = Counter(self.grid)
        for val, count in counts.items():
            if count >= 3:
                return val
        return None

    def _format_val(self, val):
        if val >= 1_000_000:
            return f"${val/1_000_000:.1f}M"
        elif val >= 1_000:
            return f"${val//1_000}K"
        return f"${val}"

    async def scratch(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your ticket!", ephemeral=True)
        if self.done:
            return

        idx = int(interaction.data["custom_id"].split("_")[1])

        # Reveal the clicked box
        for b in self.children:
            if hasattr(b, "custom_id") and b.custom_id == f"scratch_{idx}":
                b.label    = self._format_val(self.grid[idx])
                b.disabled = True
                b.style    = discord.ButtonStyle.primary
                self.revealed += 1
                break

        # Check if we have 3 matching revealed values so far
        from collections import Counter
        revealed_vals = []
        for b in self.children:
            if hasattr(b, "custom_id") and b.label != "🎴":
                try:
                    i = int(b.custom_id.split("_")[1])
                    revealed_vals.append(self.grid[i])
                except:
                    pass

        counts    = Counter(revealed_vals)
        winner_val = next((v for v, c in counts.items() if c >= 3), None)

        if winner_val:
            # Won! Reveal all remaining boxes and declare win
            self.done = True
            uid       = str(self.ctx.author.id)
            self.data[uid]["wallet"] += winner_val
            await save_db(self.data)
            for b in self.children:
                if hasattr(b, "custom_id") and b.label == "🎴":
                    try:
                        i      = int(b.custom_id.split("_")[1])
                        b.label = self._format_val(self.grid[i])
                        b.style = discord.ButtonStyle.secondary
                    except:
                        pass
                b.disabled = True
            content = f"🎉 **WINNER!** You matched three **{self._format_val(winner_val)}** and won **${winner_val:,}**!"
            return await interaction.response.edit_message(content=content, view=self)

        # No win yet — check if all boxes revealed with no winner
        if self.revealed == 9:
            self.done = True
            for b in self.children:
                b.disabled = True
            return await interaction.response.edit_message(
                content="😔 No match this time. Better luck next time!", view=self
            )

        await interaction.response.edit_message(view=self)


@bot.command(aliases=["store"])
async def shop(ctx):
    view = ShopView(ctx)
    await ctx.send(embed=view.shop_embed(), view=view)


@bot.command()
async def buy(ctx, item: str = None):
    if not item:
        return await ctx.send("❌ Specify an item. Use `.shop` to see available items.")
    item = item.lower()
    if item not in SHOP_ITEMS:
        return await ctx.send(f"❌ Item `{item}` not found. Use `.shop` to see available items.")

    data      = await get_db()
    ensure_user(data, ctx.author.id)
    uid       = str(ctx.author.id)
    shop_item = SHOP_ITEMS[item]
    cost      = shop_item["cost"]

    if data[uid]["wallet"] < cost:
        return await ctx.send(f"❌ You need ${cost:,} in your wallet. You have ${data[uid]['wallet']:,}.")

    data[uid]["wallet"] -= cost

    # XP Boosters
    if item.startswith("xp"):
        duration = shop_item["duration"]
        boost    = shop_item["boost"]
        if duration == -1:
            data[uid]["booster_end"]        = float("inf")
            data[uid]["booster_multiplier"] = boost
            await save_db(data)
            return await ctx.send(f"♾️ Lifetime {boost}x XP Booster activated! Your XP is permanently boosted.")
        else:
            seconds     = duration * 3600
            current_end = data[uid].get("booster_end", 0)
            data[uid]["booster_end"]        = max(current_end, time.time()) + seconds
            data[uid]["booster_multiplier"] = boost
            await save_db(data)
            return await ctx.send(f"{shop_item['emoji']} {shop_item['name']} activated! {boost}x XP for {duration} hours.")

    # Guardian role
    if item == "guardian":
        guild = ctx.guild
        role  = guild.get_role(GUARDIAN_ROLE_ID) if guild else None
        if not role:
            data[uid]["wallet"] += cost
            await save_db(data)
            return await ctx.send("❌ Guardian role not found. Contact an admin.")
        if role in ctx.author.roles:
            data[uid]["wallet"] += cost
            await save_db(data)
            return await ctx.send("❌ You already have the Guardian role.")
        try:
            await ctx.author.add_roles(role)
            current_nick = ctx.author.nick or ctx.author.name
            strike       = "".join(f"{c}\u0336" for c in current_nick)
            await ctx.author.edit(nick=f"{strike}\u02da")
        except discord.Forbidden:
            pass
        await save_db(data)
        return await ctx.send(f"🗡️ You purchased the G̶u̶a̶r̶d̶i̶a̶n̶˚ role for ${cost:,}!")

    # Lottery tickets
    if item in ("bronze", "silver", "gold"):
        await save_db(data)
        embed = discord.Embed(
            title=f"{shop_item['emoji']} {shop_item['name']} — Scratch Card",
            description="Click all 9 boxes to reveal!\nMatch 3 of the same to win!",
            color=0xFFD700
        )
        return await ctx.send(embed=embed, view=ScratchView(ctx, item, data))

    await save_db(data)



# ─────────────────────────────────────────────
# ADMIN COMMANDS
# ─────────────────────────────────────────────

@bot.command()
@commands.has_permissions(administrator=True)
async def addmoney(ctx, member: discord.Member, amount: int, location: str = "wallet"):
    """Add or remove money. Use negative number to deduct. .addmoney @user 5000 wallet"""
    data = await get_db(); ensure_user(data, member.id); uid = str(member.id)
    loc  = location.lower()
    if loc not in ("wallet", "bank"):
        return await ctx.send("❌ Location must be `wallet` or `bank`.")
    data[uid][loc] = max(0, data[uid][loc] + amount)
    await save_db(data)
    action = "Added" if amount >= 0 else "Removed"
    await ctx.send(f"✅ {action} **${abs(amount):,}** {'to' if amount >= 0 else 'from'} {member.display_name}'s **{loc}**. New balance: **${data[uid][loc]:,}**")

@bot.command()
@commands.has_permissions(administrator=True)
async def setmoney(ctx, member: discord.Member, amount: int, location: str = "wallet"):
    """Set exact money amount. .setmoney @user 10000 wallet"""
    data = await get_db(); ensure_user(data, member.id); uid = str(member.id)
    loc  = location.lower()
    if loc not in ("wallet", "bank"):
        return await ctx.send("❌ Location must be `wallet` or `bank`.")
    data[uid][loc] = max(0, amount)
    await save_db(data)
    await ctx.send(f"✅ Set {member.display_name}'s **{loc}** to **${amount:,}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def addxp(ctx, member: discord.Member, amount: int):
    """Add or remove XP. .addxp @user 500"""
    data = await get_db(); ensure_user(data, member.id); uid = str(member.id)
    data[uid]["xp"] = max(0, data[uid]["xp"] + amount)
    await save_db(data)
    action = "Added" if amount >= 0 else "Removed"
    await ctx.send(f"✅ {action} **{abs(amount)} XP** {'to' if amount >= 0 else 'from'} {member.display_name}. Current XP: **{data[uid]['xp']}**")

@bot.command()
@commands.has_permissions(administrator=True)
async def setlevel(ctx, member: discord.Member, level: int):
    """Set a user's level. .setlevel @user 10"""
    if level < 1:
        return await ctx.send("❌ Level must be at least 1.")
    data = await get_db(); ensure_user(data, member.id); uid = str(member.id)
    data[uid]["level"] = level
    data[uid]["xp"]    = 0
    await save_db(data)
    await ctx.send(f"✅ Set {member.display_name}'s level to **{level}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def addcredits(ctx, member: discord.Member, amount: int):
    """Add or remove credits. .addcredits @user 100"""
    data = await get_db(); ensure_user(data, member.id); uid = str(member.id)
    data[uid]["credits"] = max(0, data[uid]["credits"] + amount)
    await save_db(data)
    action = "Added" if amount >= 0 else "Removed"
    await ctx.send(f"✅ {action} **{abs(amount)} credits** {'to' if amount >= 0 else 'from'} {member.display_name}. Total: **{data[uid]['credits']:,}**")

@bot.command()
@commands.has_permissions(administrator=True)
async def resetuser(ctx, member: discord.Member):
    """Fully reset a user's data. .resetuser @user"""
    data = await get_db()
    uid  = str(member.id)
    if uid in data:
        del data[uid]
    await save_db(data)
    await ctx.send(f"🗑️ Reset all data for **{member.display_name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def userinfo(ctx, member: discord.Member = None):
    """View full data for a user. .userinfo @user"""
    t    = member or ctx.author
    data = await get_db(); ensure_user(data, t.id); u = data[str(t.id)]
    embed = discord.Embed(title=f"🔍 {t.display_name}'s Data", color=0x5865F2)
    embed.set_thumbnail(url=t.display_avatar.url)
    embed.add_field(name="💰 Economy",   value=f"Wallet: ${u['wallet']:,}\nBank: ${u['bank']:,}\nCredits: {u['credits']:,}", inline=True)
    embed.add_field(name="📊 Level",     value=f"Level: {u['level']}\nXP: {u['xp']}\nPrestige: {u.get('prestige',0)}", inline=True)
    embed.add_field(name="✨ Social",    value=f"Charms: {u['charms']:,}\nPartner: {'<@'+str(u['partner'])+'>' if u['partner'] else 'None'}", inline=True)
    rem = u.get("booster_end", 0) - time.time()
    embed.add_field(name="🚀 Booster",   value=str(datetime.timedelta(seconds=int(rem))) if rem > 0 else "None", inline=True)
    embed.add_field(name="🍵 Blacktea",  value=f"{u.get('blacktea_wins',0)} wins", inline=True)
    embed.add_field(name="💬 Messages",  value=f"Weekly: {u.get('weekly_messages',0):,}", inline=True)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def serverinfo(ctx):
    """Show economy stats for the server."""
    data  = await get_db()
    total = sum(u.get("wallet", 0) + u.get("bank", 0) for u in data.values())
    embed = discord.Embed(title="📊 Server Economy Stats", color=0x5865F2)
    embed.add_field(name="Registered Users",       value=f"{len(data):,}")
    embed.add_field(name="Total Money in Economy", value=f"${total:,}")
    embed.add_field(name="Server Members",         value=f"{ctx.guild.member_count:,}")
    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
# START
# ─────────────────────────────────────────────
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ TOKEN NOT FOUND! Check your .env file.")
