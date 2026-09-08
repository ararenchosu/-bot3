import discord
from discord import Embed, Color
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta, timezone
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# ======================================================
# ✅ 基本設定
# ======================================================
JST = timezone(timedelta(hours=9))

# 🔑 Spotify API 認証情報
SPOTIFY_CLIENT_ID = "319cd52e6587461cbf15092a69dc8b2b"
SPOTIFY_CLIENT_SECRET = "2ffae714f3c144d78154d4601ef7081e"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8080/callback"
SPOTIFY_SCOPE = "user-read-currently-playing"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# ユーザー情報保存用
user_tokens = {}
np_sessions = {}

# ======================================================
# 🎵 共通：曲情報を取得してEmbed作成
# ======================================================
async def get_nowplaying_embed(uid):
    try:
        token_info = user_tokens[uid]
        sp = spotipy.Spotify(auth=token_info["access_token"])
        current = sp.currently_playing()

        if not current or not current.get("item"):
            return "🎵 現在再生中の曲はありません。Spotifyで曲を再生してからもう一度お試しください！"

        item = current["item"]
        track_name = item["name"]
        artists = ", ".join([a["name"] for a in item["artists"]])
        album_name = item["album"]["name"]
        album_image = item["album"]["images"][0]["url"] if item["album"]["images"] else None
        duration_ms = item["duration_ms"]
        progress_ms = current["progress_ms"]
        spotify_url = item["external_urls"]["spotify"]
        is_playing = current.get("is_playing", False)

        def ms2mmss(ms):
            m = ms // 60000
            s = (ms % 60000) // 1000
            return f"{m}:{s:02d}"

        progress_str = ms2mmss(progress_ms)
        duration_str = ms2mmss(duration_ms)

        bar_len = 15
        done = max(1, int(bar_len * progress_ms / duration_ms))
        bar = "●" + "━" * (done - 1) + "●" + "─" * (bar_len - done - 1) if done > 0 else "●" + "─" * (bar_len - 1)
        percent = int(progress_ms / duration_ms * 100)

        status = "▶️ 再生中" if is_playing else "⏸️ 一時停止"
        em = Embed(
            title="🎵 今聴いている曲",
            description=f"{status} ・ {percent}% 再生中",
            color=Color.green() if is_playing else Color.orange(),
            timestamp=datetime.now(JST)
        )
        if album_image:
            em.set_thumbnail(url=album_image)
        em.add_field(name="🎶 曲名", value=f"**{track_name}**", inline=False)
        em.add_field(name="🎤 アーティスト", value=artists, inline=True)
        em.add_field(name="💿 アルバム", value=album_name, inline=True)
        em.add_field(name="⏱️ 再生時間", value=f"{progress_str} / {duration_str}", inline=True)
        em.add_field(name="📊 進行", value=f"`{bar}`", inline=False)
        em.add_field(name="🔗 Spotifyで開く", value=f"[👉 クリックで再生]({spotify_url})", inline=False)
        em.set_footer(text=f"リクエスト: {bot.get_user(int(uid)).display_name if bot.get_user(int(uid)) else '不明'}")

        return em

    except Exception as e:
        return f"❌ エラー: ```{e}```"


# ======================================================
# 🎵 /nowplaying → 曲表示＆自動更新
# ======================================================
@bot.command(name="nowplaying", aliases=["np"])
async def nowplaying_cmd(ctx):
    uid = str(ctx.author.id)

    if uid not in user_tokens or not user_tokens[uid].get("access_token"):
        sp_oauth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=SPOTIFY_SCOPE,
            show_dialog=True
        )
        auth_url = sp_oauth.get_authorize_url()
        em = Embed(
            title="🎵 Spotify連携が必要です",
            description=(
                "初めて使うときは、まずSpotifyアカウントと連携してください！\n\n"
                f"👉 **ここをクリックして認証**：[Spotify認証URL]({auth_url})\n\n"
                "✅ 認証後、ブラウザに表示されたURLをコピーして `/link-spotify [URL]` と打てば完了！"
            ),
            color=Color.green()
        )
        await ctx.send(embed=em, ephemeral=True)
        return

    em = await get_nowplaying_embed(uid)
    if isinstance(em, str):
        await ctx.send(em, ephemeral=True)
        return

    msg = await ctx.send(embed=em)
    np_sessions[uid] = {"msg": msg}


# ======================================================
# 🔗 /link-spotify → 連携
# ======================================================
@bot.command(name="link-spotify")
async def link_spotify_cmd(ctx, callback_url: str):
    uid = str(ctx.author.id)
    try:
        sp_oauth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=SPOTIFY_SCOPE
        )
        code = sp_oauth.parse_response_code(callback_url)
        token_info = sp_oauth.get_access_token(code, check_cache=False)
        user_tokens[uid] = token_info

        em = Embed(
            title="✅ Spotify連携 完了！",
            description="これから `/nowplaying` で今聴いてる曲を表示できます！\n"
                        "表示は15秒ごとに自動更新され、次の曲に行けば自動で切り替わります！",
            color=Color.green()
        )
        await ctx.send(embed=em, ephemeral=True)

    except Exception as e:
        em = Embed(title="❌ 連携に失敗", description=f"```{e}```\nURLが正しいか確認してください！", color=Color.red())
        await ctx.send(embed=em, ephemeral=True)


# ======================================================
# 🔄 15秒ごとに自動更新
# ======================================================
@tasks.loop(seconds=15)
async def update_loop():
    to_del = []
    for uid, data in list(np_sessions.items()):
        try:
            em = await get_nowplaying_embed(uid)
            if isinstance(em, str):
                to_del.append(uid)
                continue
            await data["msg"].edit(embed=em)
        except:
            to_del.append(uid)
    for uid in to_del:
        np_sessions.pop(uid, None)


# ======================================================
# 🚀 起動
# ======================================================
@bot.event
async def on_ready():
    print(f"✅ ログイン完了: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="/nowplaying で曲を共有"))
    if not update_loop.is_running():
        update_loop.start()


if __name__ == "__main__":
    load_dotenv()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ DISCORD_TOKEN が見つかりません")
        exit()
    bot.run(TOKEN)
