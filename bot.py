import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp as youtube_dl
from dotenv import load_dotenv
import os
import asyncio
from flask import Flask
from threading import Thread

# Загрузить токен из .env
load_dotenv()

# Настройки для yt-dlp
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt'  # Использование cookies.txt
}

# Настройки FFmpeg
ffmpeg_options = {
    # 'executable': './ffmpeg/bin/ffmpeg.exe',
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# Класс для загрузки аудио с YouTube
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# Настройка бота
intents = discord.Intents.default()
intents.message_content = True  # Включаем доступ к содержимому сообщений
intents.members = True  # Включаем Server Members Intent

bot = commands.Bot(command_prefix="!", intents=intents)

# Очередь воспроизведения
queue = []

# Текущее значение таймера для автоматического отключения
disconnect_timer = None

# Обработка ошибок воспроизведения
def after_playing(error):
    global disconnect_timer
    if error:
        print(f'Ошибка воспроизведения: {error}')
    else:
        print('Воспроизведение завершено.')
        # Переходим к следующему элементу в очереди
        if queue:
            next_url = queue.pop(0)
            asyncio.run_coroutine_threadsafe(play_next(next_url), bot.loop)
        else:
            # Если очередь пуста, запускаем таймер для отключения
            disconnect_timer = asyncio.run_coroutine_threadsafe(start_disconnect_timer(), bot.loop)

# Таймер
async def start_disconnect_timer():
    await asyncio.sleep(300)  # 5 минут (300 секунд)
    await auto_disconnect()

# Выход по истечении таймера
async def auto_disconnect():
    global disconnect_timer
    for voice_client in bot.voice_clients:
        if not voice_client.is_playing() and not queue:
            await voice_client.disconnect()
            print("Бот отключен из-за бездействия.")
    disconnect_timer = None

# Функция проигрывания следующего трека из списка
async def play_next(url):
    global disconnect_timer
    voice_client = discord.utils.get(bot.voice_clients)
    if voice_client and voice_client.is_connected():
        async with voice_client.channel.typing():
            player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
            voice_client.play(player, after=after_playing)
        await voice_client.channel.send(f'Сейчас играет: {player.title}')
        # Отменяем таймер отключения, если он был запущен
        if disconnect_timer:
            disconnect_timer.cancel()
            disconnect_timer = None

# Синхронизация Slash-команд
@bot.event
async def on_ready():
    print(f'Бот {bot.user.name} готов к работе!')
    try:
        synced = await bot.tree.sync()
        print(f"Синхронизировано {len(synced)} команд.")
    except Exception as e:
        print(f"Ошибка синхронизации команд: {e}")

# Slash-команда для воспроизведения аудио с YouTube
@bot.tree.command(name="play", description="Воспроизводит аудио с YouTube")
async def play(interaction: discord.Interaction, url: str):
    global disconnect_timer
    # Проверка, что пользователь находится в голосовом канале
    if not interaction.user.voice:
        await interaction.response.send_message(f"{interaction.user.name}, вы должны быть в голосовом канале.", ephemeral=True)
        return

    # Получаем голосовой канал пользователя
    channel = interaction.user.voice.channel

    # Если бот уже подключен к голосовому каналу, но не к тому, где находится пользователь
    if interaction.guild.voice_client:
        if interaction.guild.voice_client.channel != channel:
            await interaction.guild.voice_client.move_to(channel)  # Перемещаем бота в канал пользователя
    else:
        # Если бот не подключен, подключаем его
        await channel.connect()

    # Получаем объект голосового клиента
    voice_client = interaction.guild.voice_client

    # Если ничего не играет, начинаем воспроизведение
    if not voice_client.is_playing():
        await interaction.response.send_message("Загрузка трека...", ephemeral=True)
        async with interaction.channel.typing():
            player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
            voice_client.play(player, after=after_playing)
        await interaction.followup.send(f'Сейчас играет: {player.title}')
    else:
        # Если что-то уже играет, добавляем в очередь
        queue.append(url)
        await interaction.response.send_message(f'Добавлено в очередь: {url}')

    # Отменяем таймер отключения, если он был запущен
    if disconnect_timer:
        disconnect_timer.cancel()
        disconnect_timer = None

# Slash-команда для остановки воспроизведения
@bot.tree.command(name="stop", description="Останавливает воспроизведение, но бот остается в канале")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await interaction.response.send_message('Воспроизведение остановлено.')
    else:
        await interaction.response.send_message('Сейчас ничего не играет.', ephemeral=True)

# Slash-команда для пропуска текущего трека
@bot.tree.command(name="next", description="Пропускает текущий трек и начинает следующий в очереди")
async def next(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        if queue:
            await interaction.response.send_message('Трек пропущен. Начинаю следующий.')
        else:
            await interaction.response.send_message('Трек пропущен, но очередь пуста.')
    else:
        await interaction.response.send_message('Сейчас ничего не играет.', ephemeral=True)

# Slash-команда для отображения очереди
@bot.tree.command(name="queue", description="Показывает текущую очередь воспроизведения")
async def show_queue(interaction: discord.Interaction):
    if queue:
        queue_list = "\n".join([f"{i + 1}. {url}" for i, url in enumerate(queue)])
        await interaction.response.send_message(f"Текущая очередь:\n{queue_list}")
    else:
        await interaction.response.send_message("Очередь пуста.", ephemeral=True)

# Slash-команда для отключения бота от голосового канала
@bot.tree.command(name="leave", description="Отключает бота от голосового канала")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Бот отключен от голосового канала.")
    else:
        await interaction.response.send_message("Бот не подключен к голосовому каналу.", ephemeral=True)

# Имитация прослушивания порта
app = Flask('')

@app.route('/')
def home():
    return "I'm alive"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Запуск HTTP-сервера
keep_alive()

# Запуск бота
bot.run(os.getenv('DISCORD_TOKEN'))