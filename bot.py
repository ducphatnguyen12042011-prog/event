import discord
from discord.ext import commands, tasks
import sqlite3
import requests
import random
import os
from datetime import datetime

TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("FOOTBALL_API")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

CASINO_LOGO = "https://i.imgur.com/8Km9tLL.png"

# ================= DATABASE =================
conn = sqlite3.connect("database.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
id INTEGER PRIMARY KEY,
cash INTEGER DEFAULT 1000000,
wins INTEGER DEFAULT 0,
loses INTEGER DEFAULT 0)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS bets(
user_id INTEGER,
fixture_id INTEGER,
team_pick TEXT,
amount INTEGER)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS history(
user_id INTEGER,
result TEXT,
amount INTEGER,
time TEXT)
""")

conn.commit()

# ================= UTIL =================
def get_user(uid):
    cursor.execute("SELECT * FROM users WHERE id=?", (uid,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users(id) VALUES(?)", (uid,))
        conn.commit()
        return get_user(uid)
    return user

def api(endpoint):
    headers = {"x-apisports-key": API_KEY}
    return requests.get(
        f"https://v3.football.api-sports.io/{endpoint}",
        headers=headers
    ).json()

def calculate_handicap(rankA, rankB):
    diff = rankA - rankB
    if diff <= -10:
        return "-1.5"
    elif diff <= -5:
        return "-1"
    elif diff <= -2:
        return "-0.5"
    else:
        return "0"

# ================= AUTO SETTLE =================
@tasks.loop(minutes=1)
async def auto_settle():
    cursor.execute("SELECT * FROM bets")
    bets = cursor.fetchall()

    for bet in bets:
        fixture_id = bet[1]
        data = api(f"fixtures?id={fixture_id}")
        if not data["response"]:
            continue

        fixture = data["response"][0]

        if fixture["fixture"]["status"]["short"] == "FT":

            home = fixture["teams"]["home"]["name"]
            away = fixture["teams"]["away"]["name"]
            home_goals = fixture["goals"]["home"]
            away_goals = fixture["goals"]["away"]

            winner = home if home_goals > away_goals else away

            if bet[2] == winner:
                cursor.execute("UPDATE users SET cash=cash+?, wins=wins+1 WHERE id=?",
                               (bet[3]*2, bet[0]))
                result = "✅ Thắng"
            else:
                cursor.execute("UPDATE users SET loses=loses+1 WHERE id=?",
                               (bet[0],))
                result = "❌ Thua"

            cursor.execute("INSERT INTO history VALUES (?,?,?,?)",
                           (bet[0], result, bet[3], datetime.now().strftime("%H:%M")))
            cursor.execute("DELETE FROM bets WHERE fixture_id=?", (fixture_id,))
            conn.commit()

# ================= WALLET =================
class WalletView(discord.ui.View):
    @discord.ui.button(label="📜 Lịch sử", style=discord.ButtonStyle.primary)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor.execute("SELECT result, amount FROM history WHERE user_id=?", (interaction.user.id,))
        data = cursor.fetchall()
        msg = ""
        for row in data[-5:]:
            msg += f"{row[0]} - {row[1]:,} VC\n"
        if msg == "":
            msg = "Chưa có dữ liệu."
        await interaction.response.send_message(msg, ephemeral=True)

@bot.command()
async def wallet(ctx):
    user = get_user(ctx.author.id)
    embed = discord.Embed(
        title="💰 VÍ VERDICT CASH",
        description=f"{user[1]:,} VC",
        color=0x2ecc71
    )
    embed.set_thumbnail(url=CASINO_LOGO)
    await ctx.send(embed=embed, view=WalletView())

# ================= BXH =================
@bot.command()
async def bxh(ctx):
    cursor.execute("SELECT id, cash FROM users ORDER BY cash DESC LIMIT 10")
    data = cursor.fetchall()

    embed = discord.Embed(
        title="✨ BẢNG XẾP HẠNG ĐẠI GIA VERDICT CASH ✨",
        color=0xf1c40f
    )
    embed.set_thumbnail(url=CASINO_LOGO)

    for i, row in enumerate(data):
        user = await bot.fetch_user(row[0])
        embed.add_field(
            name=f"#{i+1} {user.name}",
            value=f"{row[1]:,} VC",
            inline=False
        )

    await ctx.send(embed=embed)

# ================= MATCH =================
@bot.command()
async def match(ctx):
    data = api("fixtures?next=5")
    embed = discord.Embed(title="⚽ TRẬN SẮP DIỄN RA", color=0xf1c40f)

    for game in data["response"]:
        home = game["teams"]["home"]["name"]
        away = game["teams"]["away"]["name"]
        logo = game["teams"]["home"]["logo"]
        fixture_id = game["fixture"]["id"]

        embed.add_field(
            name=f"{home} vs {away}",
            value=f"ID: {fixture_id}",
            inline=False
        )
        embed.set_thumbnail(url=logo)

    await ctx.send(embed=embed)

# ================= BET =================
@bot.command()
async def bet(ctx, fixture_id: int, team: str, amount: int):
    user = get_user(ctx.author.id)
    if user[1] < amount:
        return await ctx.send("❌ Không đủ tiền.")

    data = api(f"standings?league=39&season=2023")
    standings = data["response"][0]["league"]["standings"][0]

    rank_map = {t["team"]["name"]: t["rank"] for t in standings}

    cursor.execute("UPDATE users SET cash=cash-? WHERE id=?",
                   (amount, ctx.author.id))
    cursor.execute("INSERT INTO bets VALUES (?,?,?,?)",
                   (ctx.author.id, fixture_id, team, amount))
    conn.commit()

    embed = discord.Embed(
        title="🎟 VÉ CƯỢC VERDICT",
        description=f"Đội chọn: {team}\nTiền cược: {amount:,} VC",
        color=0xf1c40f
    )
    embed.set_thumbnail(url=CASINO_LOGO)

    await ctx.author.send(embed=embed)
    await ctx.send("📩 Vé đã gửi DM.")

# ================= SHOP =================
SHOP = {"VIP": 200000, "LuckyCharm": 300000}

class ShopView(discord.ui.View):
    def __init__(self, item, price):
        super().__init__()
        self.item = item
        self.price = price

    @discord.ui.button(label="Đổi ngay", style=discord.ButtonStyle.success)
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = get_user(interaction.user.id)
        if user[1] < self.price:
            return await interaction.response.send_message("❌ Không đủ tiền.", ephemeral=True)

        cursor.execute("UPDATE users SET cash=cash-? WHERE id=?",
                       (self.price, interaction.user.id))
        conn.commit()

        channel = await interaction.guild.create_text_channel(f"ticket-{self.item}")
        await channel.set_permissions(interaction.user, read_messages=True, send_messages=True)

        await interaction.response.send_message("🎫 Đã tạo ticket.", ephemeral=True)

@bot.command()
async def shop(ctx):
    embed = discord.Embed(title="🛒 SHOP VERDICT", color=0x9b59b6)
    for item, price in SHOP.items():
        embed.add_field(name=item, value=f"{price:,} VC", inline=False)

    await ctx.send(embed=embed, view=ShopView("VIP", SHOP["VIP"]))

# ================= TÀI XỈU =================
@bot.command()
async def taixiu(ctx, amount: int):
    user = get_user(ctx.author.id)
    if user[1] < amount:
        return await ctx.send("❌ Không đủ tiền.")

    if random.random() < 0.53:
        result = "❌ Thua"
        cursor.execute("UPDATE users SET cash=cash-?, loses=loses+1 WHERE id=?",
                       (amount, ctx.author.id))
    else:
        result = "✅ Thắng"
        cursor.execute("UPDATE users SET cash=cash+?, wins=wins+1 WHERE id=?",
                       (amount, ctx.author.id))

    cursor.execute("INSERT INTO history VALUES (?,?,?,?)",
                   (ctx.author.id, result, amount, datetime.now().strftime("%H:%M")))
    conn.commit()

    embed = discord.Embed(title="🎲 TÀI XỈU", description=result,
                          color=0xe74c3c if "Thua" in result else 0x2ecc71)
    await ctx.send(embed=embed)

# ================= SOI CẦU =================
@bot.command()
async def cau(ctx):
    cursor.execute("SELECT result FROM history WHERE user_id=?", (ctx.author.id,))
    data = cursor.fetchall()

    pattern = ""
    for row in data[-10:]:
        if "Thắng" in row[0]:
            pattern += "🟢 "
        else:
            pattern += "🔴 "

    if pattern == "":
        pattern = "Chưa có dữ liệu."

    await ctx.send(f"🔥 SOI CẦU:\n{pattern}")

@bot.event
async def on_ready():
    auto_settle.start()
    print("Bot Online")

bot.run(TOKEN)
