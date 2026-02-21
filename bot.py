import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
import sqlite3
import requests
import os
import random
from datetime import datetime

TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("FOOTBALL_API")

MATCH_CHANNEL_ID = 1474672512708247582
BXH_CHANNEL_ID = 1474674662792232981

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= DATABASE =================
conn = sqlite3.connect("verdict.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    cash INTEGER DEFAULT 100000
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS bets(
    user_id INTEGER,
    fixture_id INTEGER,
    team TEXT,
    amount INTEGER,
    handicap REAL,
    stronger TEXT,
    settled INTEGER DEFAULT 0
)
""")

conn.commit()

# ================= API =================
def api(endpoint):
    headers = {"x-apisports-key": API_KEY}
    return requests.get(
        f"https://v3.football.api-sports.io/{endpoint}",
        headers=headers
    ).json()

def get_user(uid):
    cursor.execute("SELECT * FROM users WHERE id=?", (uid,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users(id) VALUES(?)", (uid,))
        conn.commit()
        return (uid, 100000)
    return user

# ================= TEAM POWER =================
def calculate_power(team):
    points = team["points"]
    diff = team["goalsDiff"]
    form = team["form"]

    form_score = form.count("W")*3 + form.count("D")
    return points*1.5 + diff*1.2 + form_score

def auto_handicap(home_data, away_data):
    home_power = calculate_power(home_data)
    away_power = calculate_power(away_data)
    diff = abs(home_power - away_power)

    if diff <= 10:
        h = 0
    elif diff <= 25:
        h = 0.5
    elif diff <= 45:
        h = 1
    elif diff <= 70:
        h = 1.5
    else:
        h = 2

    stronger = "home" if home_power > away_power else "away"
    return stronger, h

# ================= AUTO MATCH =================
@tasks.loop(minutes=30)
async def auto_match():
    ch = bot.get_channel(MATCH_CHANNEL_ID)
    if not ch:
        return

    data = api("fixtures?next=5")

    embed = discord.Embed(title="⚽ KÈO SẮP MỞ", color=0xf1c40f)

    for g in data["response"]:
        fid = g["fixture"]["id"]
        league = g["league"]["id"]
        season = g["league"]["season"]

        home = g["teams"]["home"]["name"]
        away = g["teams"]["away"]["name"]

        standings = api(f"standings?league={league}&season={season}")
        table = standings["response"][0]["league"]["standings"][0]

        home_data = next(t for t in table if t["team"]["name"]==home)
        away_data = next(t for t in table if t["team"]["name"]==away)

        stronger, h = auto_handicap(home_data, away_data)
        strong_name = home if stronger=="home" else away

        embed.add_field(
            name=f"{home} vs {away}",
            value=f"ID: {fid}\nKèo: {strong_name} -{h}",
            inline=False
        )

    await ch.send(embed=embed)

# ================= AUTO BXH =================
@tasks.loop(hours=1)
async def auto_bxh():
    ch = bot.get_channel(BXH_CHANNEL_ID)
    if not ch:
        return

    cursor.execute("SELECT id,cash FROM users ORDER BY cash DESC LIMIT 10")
    data = cursor.fetchall()

    embed = discord.Embed(
        title="✨ BẢNG XẾP HẠNG ĐẠI GIA VERDICT CASH ✨",
        color=0xf1c40f
    )

    for i,u in enumerate(data):
        user = await bot.fetch_user(u[0])
        embed.add_field(name=f"#{i+1} {user.name}",
                        value=f"{u[1]:,} VC",
                        inline=False)

    await ch.send(embed=embed)

# ================= AUTO SETTLE =================
@tasks.loop(minutes=5)
async def auto_settle():
    cursor.execute("SELECT * FROM bets WHERE settled=0")
    bets = cursor.fetchall()

    for b in bets:
        uid,fid,team,amount,handicap,stronger,settled = b

        data = api(f"fixtures?id={fid}")
        if not data["response"]:
            continue

        f = data["response"][0]
        if f["fixture"]["status"]["short"] != "FT":
            continue

        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        hg = f["goals"]["home"]
        ag = f["goals"]["away"]

        if stronger=="home":
            hg -= handicap
        else:
            ag -= handicap

        winner = home if hg>ag else away

        if team==winner:
            cursor.execute("UPDATE users SET cash=cash+? WHERE id=?",
                           (amount*2,uid))

        cursor.execute("UPDATE bets SET settled=1 WHERE user_id=? AND fixture_id=?",
                       (uid,fid))
        conn.commit()

# ================= COMMANDS =================

@bot.command()
async def wallet(ctx):
    u = get_user(ctx.author.id)
    await ctx.send(f"💰 Số dư: {u[1]:,} Verdict Cash")

@bot.command()
async def bet(ctx, fixture_id:int, team:str, amount:int):
    u = get_user(ctx.author.id)
    if u[1] < amount:
        return await ctx.send("❌ Không đủ tiền.")

    data = api(f"fixtures?id={fixture_id}")
    if not data["response"]:
        return await ctx.send("❌ Sai ID.")

    match = data["response"][0]
    home = match["teams"]["home"]["name"]
    away = match["teams"]["away"]["name"]

    league = match["league"]["id"]
    season = match["league"]["season"]

    standings = api(f"standings?league={league}&season={season}")
    table = standings["response"][0]["league"]["standings"][0]

    home_data = next(t for t in table if t["team"]["name"]==home)
    away_data = next(t for t in table if t["team"]["name"]==away)

    stronger,h = auto_handicap(home_data,away_data)

    cursor.execute("UPDATE users SET cash=cash-? WHERE id=?",
                   (amount,ctx.author.id))

    cursor.execute("""
    INSERT INTO bets(user_id,fixture_id,team,amount,handicap,stronger)
    VALUES(?,?,?,?,?,?)
    """,(ctx.author.id,fixture_id,team,amount,h,stronger))

    conn.commit()

    embed = discord.Embed(
        title="🎟 VÉ CƯỢC",
        description=f"{home} vs {away}\nKèo: {(home if stronger=='home' else away)} -{h}\nBạn chọn: {team}\nTiền: {amount:,}",
        color=0x3498db
    )

    await ctx.author.send(embed=embed)
    await ctx.send("✅ Đã gửi vé qua DM.")

@bot.command()
async def tx(ctx, choice:str, amount:int):
    choice = choice.lower()
    if choice not in ["tài","xỉu"]:
        return await ctx.send("❌ nhập tài hoặc xỉu")

    u = get_user(ctx.author.id)
    if u[1] < amount:
        return await ctx.send("❌ Không đủ tiền.")

    roll = random.randint(1,6)+random.randint(1,6)
    result_type = "tài" if roll>=7 else "xỉu"

    if random.random()<0.53:
        win=False
    else:
        win = (choice==result_type)

    if win:
        cursor.execute("UPDATE users SET cash=cash+? WHERE id=?",
                       (amount,ctx.author.id))
        msg="✅ Thắng"
    else:
        cursor.execute("UPDATE users SET cash=cash-? WHERE id=?",
                       (amount,ctx.author.id))
        msg="❌ Thua"

    conn.commit()
    await ctx.send(f"🎲 Ra {roll} → {msg}")

# ================= READY =================
@bot.event
async def on_ready():
    auto_match.start()
    auto_bxh.start()
    auto_settle.start()
    print("Bot Online")

bot.run(TOKEN)
