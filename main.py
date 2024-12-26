import os
from dotenv import load_dotenv
import discord
from discord import app_commands
import matplotlib.pyplot as plt
import yfinance as yf
from discord.ext import commands, tasks
from discord.ui import Button, View
import datetime
import requests
import csv
import io
import json
import pandas as pd
import asyncio
import pandas_market_calendars as mcal

load_dotenv()
TOKEN = os.getenv('TOKEN')

intents = discord.Intents.default()
intents.message_content = True
client = commands.Bot(command_prefix="!", intents=intents)

TICKER_FILE = "server_tickers.json"
nyse = mcal.get_calendar('NYSE')

def get_market_status():
    """Get the current market status and time until next open/close."""
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date()

    # Check if today is a market holiday
    schedule = nyse.schedule(start_date=today, end_date=today)
    if schedule.empty:
        return "📅 Market closed (holiday)"

    # Get market open and close times
    market_open = schedule.iloc[0]['market_open'].to_pydatetime()
    market_close = schedule.iloc[0]['market_close'].to_pydatetime()

    if now < market_open:
        time_until_open = market_open - now
        return f"⏳ Opens in {time_until_open.seconds // 3600}h {(time_until_open.seconds % 3600) // 60}m"
    elif now < market_close:
        time_until_close = market_close - now
        return f"⏳ Closes in {time_until_close.seconds // 3600}h {(time_until_close.seconds % 3600) // 60}m"
    else:
        return "📅 Market closed"

@tasks.loop(minutes=1)  # Update every minute
async def update_bot_status():
    """Update the bot's status with the market status."""
    status = get_market_status()
    activity = discord.Activity(type=discord.ActivityType.watching, name=status)
    await client.change_presence(activity=activity)

@update_bot_status.before_loop
async def before_update_bot_status():
    """Wait for the bot to be ready before starting the task."""
    await client.wait_until_ready()

def load_tickers():
    """Load server-specific ticker lists from the JSON file."""
    if os.path.exists(TICKER_FILE):
        with open(TICKER_FILE, "r") as file:
            return json.load(file)
    return {}

def save_tickers(tickers):
    """Save server-specific ticker lists to the JSON file."""
    with open(TICKER_FILE, "w") as file:
        json.dump(tickers, file, indent=4)

def fetch_stock_events(ticker):
    """Fetch stock market events using the yfinance library."""
    try:
        stock = yf.Ticker(ticker)
        calendar = stock.calendar

        # Check if calendar is valid
        if calendar is None:
            print(f"No earnings dates found for {ticker}.")
            return []

        events = []
        if isinstance(calendar, dict):
            # Handle dictionary format
            earnings_dates = calendar.get("Earnings Date", [])
            if not earnings_dates:
                print(f"No earnings dates found for {ticker}.")
                return []

            # Sort earnings dates and use the earliest one
            earnings_dates.sort()
            event_date = datetime.datetime.combine(earnings_dates[0], datetime.time()).replace(tzinfo=datetime.timezone.utc)
            event_dates = [date.strftime("%Y-%m-%d") for date in earnings_dates]
            event_description = f"Earnings reports for {ticker} on: " + ", ".join(event_dates)
            events.append({
                "name": f"Earnings: {ticker}",
                "date": event_date,
                "description": event_description
            })

        elif isinstance(calendar, pd.DataFrame):
            # Handle DataFrame format
            if calendar.empty:
                print(f"No earnings dates found for {ticker}.")
                return []

            # Sort earnings dates and use the earliest one
            calendar = calendar.sort_values(by="Earnings Date")
            event_date = calendar.iloc[0]["Earnings Date"].to_pydatetime().replace(tzinfo=datetime.timezone.utc)
            event_dates = [row["Earnings Date"].strftime("%Y-%m-%d") for _, row in calendar.iterrows()]
            event_description = f"Earnings reports for {ticker} on: " + ", ".join(event_dates)
            events.append({
                "name": f"Earnings: {ticker}",
                "date": event_date,
                "description": event_description
            })

        else:
            print(f"Unexpected calendar format for {ticker}.")
            return []

        return events

    except Exception as e:
        print(f"Failed to fetch stock events for {ticker}: {e}")
        return []

async def create_discord_events(guild, events):
    """Create Discord events for the given stock events."""
    # Fetch existing events
    existing_events = await guild.fetch_scheduled_events()

    for event in events:
        # Check if the event already exists
        if not any(
            e.name == event["name"]
            for e in existing_events
        ):
            try:
                # Set end_time to 1 hour after start_time
                end_time = event["date"] + datetime.timedelta(hours=1)
                await guild.create_scheduled_event(
                    name=event["name"],
                    start_time=event["date"],
                    end_time=end_time,
                    description=event["description"],
                    entity_type=discord.EntityType.external,
                    location="Stock Market",
                    privacy_level=discord.PrivacyLevel.guild_only
                )
                print(f"Created event: {event['name']}")
                # Add a small delay to avoid race conditions
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Failed to create event {event['name']}: {e}")
        else:
            print(f"Event already exists: {event['name']}")

@tasks.loop(hours=24)  # Run daily
async def update_stock_events():
    """Periodically update stock events in the server."""
    tickers = load_tickers()
    for guild_id, ticker_list in tickers.items():
        guild = client.get_guild(int(guild_id))
        if guild:
            for ticker in ticker_list:
                events = fetch_stock_events(ticker)
                await create_discord_events(guild, events)

@update_stock_events.before_loop
async def before_update_stock_events():
    """Wait for the bot to be ready before starting the task."""
    await client.wait_until_ready()

def generate_stock_chart(ticker: str, period: str):
    try:
        stock = yf.Ticker(ticker)
        history = stock.history(period=period)

        if history.empty:
            return None, f"No data found for ticker '{ticker}' in period '{period}'."

        last_price = history['Close'].iloc[-1]
        high_price = history['High'].max()
        low_price = history['Low'].min()

        plt.figure(figsize=(12, 6))
        plt.plot(history.index, history['Close'], label=f"{ticker.upper()} Closing Prices", color='tab:blue', linewidth=2)
        plt.title(f"{ticker.upper()} Stock Prices ({period})", fontsize=16)
        plt.xlabel("Date", fontsize=12)
        plt.ylabel("Price (USD)", fontsize=12)
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.annotate(f"Last: {last_price:.2f}\nHigh: {high_price:.2f}\nLow: {low_price:.2f}",
                     xy=(0.5, 0.9), xycoords='axes fraction', ha='center', fontsize=10,
                     bbox=dict(facecolor='white', alpha=0.7, boxstyle="round,pad=0.5"))
        plt.legend()
        plt.tight_layout()

        chart_path = f"{ticker}_chart.png"
        plt.savefig(chart_path)
        plt.close()
        return chart_path, None

    except Exception as e:
        return None, str(e)

class StockChartView(View):
    def __init__(self, ticker, period):
        super().__init__(timeout=300)
        self.ticker = ticker
        self.period = period

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        chart_path, error = generate_stock_chart(self.ticker, self.period)
        if error:
            await interaction.followup.send(f"Error: {error}", ephemeral=True)
        else:
            with open(chart_path, 'rb') as file:
                await interaction.followup.send(file=discord.File(file, filename=f"{self.ticker}_chart.png"), view=self)
            os.remove(chart_path)

    @discord.ui.button(label="Y", style=discord.ButtonStyle.secondary)
    async def year(self, interaction: discord.Interaction, button: Button):
        """Set period to 1 year."""
        self.period = "1y"
        await self.update_chart(interaction)

    @discord.ui.button(label="M", style=discord.ButtonStyle.secondary)
    async def month(self, interaction: discord.Interaction, button: Button):
        """Set period to 1 month."""
        self.period = "1mo"
        await self.update_chart(interaction)

    @discord.ui.button(label="W", style=discord.ButtonStyle.secondary)
    async def week(self, interaction: discord.Interaction, button: Button):
        """Set period to 1 week."""
        self.period = "5d"
        await self.update_chart(interaction)

    @discord.ui.button(label="D", style=discord.ButtonStyle.secondary)
    async def day(self, interaction: discord.Interaction, button: Button):
        """Set period to 1 day."""
        self.period = "1d"
        await self.update_chart(interaction)

    @discord.ui.button(label="H", style=discord.ButtonStyle.secondary)
    async def hour(self, interaction: discord.Interaction, button: Button):
        """Set period to 1 hour."""
        self.period = "1h"
        await self.update_chart(interaction)

    @discord.ui.button(label="m", style=discord.ButtonStyle.secondary)
    async def minute(self, interaction: discord.Interaction, button: Button):
        """Set period to 1 minute."""
        self.period = "1m"
        await self.update_chart(interaction)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: Button):
        """Delete the message."""
        await interaction.message.delete()

    async def update_chart(self, interaction):
        """Update the chart with the new period and send the message again."""
        await interaction.response.defer()
        chart_path, error = generate_stock_chart(self.ticker, self.period)
        if error:
            await interaction.followup.send(f"Error: {error}", ephemeral=True)
        else:
            with open(chart_path, 'rb') as file:
                await interaction.followup.send(file=discord.File(file, filename=f"{self.ticker}_chart.png"), view=self)

@client.tree.command(name="ch", description="Show the stock chart for a ticker")
async def show_stock_chart(interaction: discord.Interaction, ticker: str, period: str = "1mo"):
    await interaction.response.defer()
    chart_path, error = generate_stock_chart(ticker, period)

    if error:
        await interaction.followup.send(f"Error: {error}")
    else:
        stock = yf.Ticker(ticker)
        history = stock.history(period=period)
        last_price = history['Close'].iloc[-1]
        high_price = history['High'].max()
        low_price = history['Low'].min()

        top_message = f"{ticker.upper()} {period} - Last: ${last_price:.2f} High: ${high_price:.2f} Low: ${low_price:.2f}"
        view = StockChartView(ticker, period)
        with open(chart_path, 'rb') as file:
            await interaction.followup.send(top_message, file=discord.File(file, filename=f"{ticker}_chart.png"), view=view)
        os.remove(chart_path)

@client.tree.command(name="add_ticker", description="Add a stock ticker to the server's list")
async def add_ticker(interaction: discord.Interaction, ticker: str):
    """Add a stock ticker to the server's list."""
    # Defer the interaction to prevent it from expiring
    await interaction.response.defer()

    tickers = load_tickers()
    guild_id = str(interaction.guild.id)

    if guild_id not in tickers:
        tickers[guild_id] = []

    if ticker.upper() not in tickers[guild_id]:
        tickers[guild_id].append(ticker.upper())
        save_tickers(tickers)

        # Fetch and create events for the new ticker immediately
        guild = interaction.guild
        events = fetch_stock_events(ticker.upper())
        await create_discord_events(guild, events)

        # Use followup.send after deferring
        await interaction.followup.send(f"Added {ticker.upper()} to the server's ticker list.")
    else:
        # Use followup.send after deferring
        await interaction.followup.send(f"{ticker.upper()} is already in the server's ticker list.")

@client.tree.command(name="remove_ticker", description="Remove a stock ticker from the server's list")
async def remove_ticker(interaction: discord.Interaction, ticker: str):
    """Remove a stock ticker from the server's list."""
    # Defer the interaction to prevent it from expiring
    await interaction.response.defer()

    tickers = load_tickers()
    guild_id = str(interaction.guild.id)

    if guild_id in tickers and ticker.upper() in tickers[guild_id]:
        tickers[guild_id].remove(ticker.upper())
        save_tickers(tickers)
        await interaction.followup.send(f"Removed {ticker.upper()} from the server's ticker list.")
    else:
        await interaction.followup.send(f"{ticker.upper()} is not in the server's ticker list.")

@client.tree.command(name="ticker_list", description="Show the currently added ticker list for this server")
async def ticker_list(interaction: discord.Interaction):
    """Show the currently added ticker list for this server."""
    # Defer the interaction to prevent it from expiring
    await interaction.response.defer()

    tickers = load_tickers()
    guild_id = str(interaction.guild.id)

    if guild_id in tickers and tickers[guild_id]:
        ticker_list_str = ", ".join(tickers[guild_id])
        await interaction.followup.send(f"Ticker list for this server: {ticker_list_str}")
    else:
        await interaction.followup.send("No tickers have been added to this server yet.")

@client.tree.command(name="clear_events", description="Clear all events in this server made by the bot")
async def clear_events(interaction: discord.Interaction):
    """Clear all events in this server made by the bot."""
    # Defer the interaction to prevent it from expiring
    await interaction.response.defer()

    guild = interaction.guild
    existing_events = await guild.fetch_scheduled_events()

    # Delete all events created by the bot
    deleted_count = 0
    for event in existing_events:
        if event.name.startswith("Earnings:"):
            try:
                await event.delete()
                deleted_count += 1
            except Exception as e:
                print(f"Failed to delete event {event.name}: {e}")

    await interaction.followup.send(f"Deleted {deleted_count} events.")

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    try:
        await client.tree.sync()
        await client.tree.sync(guild=discord.Object(1099010752024694905))
        print("Slash commands synced")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    if not update_stock_events.is_running():
        update_stock_events.start()

    if not update_bot_status.is_running():
        update_bot_status.start()

client.run(TOKEN)