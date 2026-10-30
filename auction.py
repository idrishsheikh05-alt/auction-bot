import os
import logging
import re
import random
import string
import json
import sqlite3
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ----------------- FLASK WEB SERVER (FOR RENDER) -----------------
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "PW Auction Bot is active and running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# ----------------- BOT CONFIGURATION & CONSTANTS -----------------
BOT_TOKEN = "9147976890:AAGcIPJ6h7JdB44mfJiob8FGYkOPC5L7syY"
ADMIN_GROUP_ID = -1003840329293
CHANNEL_ID = "@pwpokes"

MAIN_GC_LINK = "https://t.me/pokeworldhexa" 
MAIN_GC_USERNAME = "@pokeworldhexa"

OWNER_ID = 6505629669
DATA_FILE = "auction_data.json"

submission_open = True
auction_started = True

bot_admins = set()          
verified_users = set()      
auction_db = {}             
pending_rejections = {}

def save_data():
    data = {
        "bot_admins": list(bot_admins),
        "verified_users": list(verified_users),
        "auction_db": auction_db
    }
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def load_data():
    global bot_admins, verified_users, auction_db
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                bot_admins = set(data.get("bot_admins", []))
                verified_users = set(data.get("verified_users", []))
                auction_db = data.get("auction_db", {})
        except Exception as e:
            logging.error(f"Error loading data: {e}")

load_data()

(CATEGORY, NAME, COLLECT_MEDIA, BOOSTED_ASK, 
 BOOSTED_SPECIFY, BASE_PRICE, CONFIRMATION, REJECT_REASON) = range(8)

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def is_admin_or_owner(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in bot_admins

def parse_price(price_str: str) -> int:
    if isinstance(price_str, (int, float)):
        return int(price_str)
    s = str(price_str).lower().strip().replace(",", "")
    if s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))

def generate_item_id():
    return ''.join(random.choices(string.ascii_letters, k=5))

async def resolve_target_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if update.message and update.message.reply_to_message:
        return update.message.reply_to_message.from_user.id
    if context.args:
        arg = context.args[0].strip()
        if arg.isdigit():
            return int(arg)
        elif arg.startswith("@"):
            target_username = arg[1:].lower()
            for item in auction_db.values():
                if item.get("seller_username", "").replace("@", "").lower() == target_username:
                    return item.get("seller_id")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = f"Hey there {user.first_name}! Welcome to PW Auction Bot\n\nYou can add your Pokémon and TMs for auction here securely and easily."
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Item to Auction", callback_data="start_sell")],
        [InlineKeyboardButton("📢 Auction Channel", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"),
         InlineKeyboardButton("👥 Main GC", url=MAIN_GC_LINK)]
    ]
    welcome_image = "https://images.unsplash.com/photo-1613771404784-3a5686aa2be3?q=80&w=1000&auto=format&fit=crop"
    
    if update.message:
        await update.message.reply_photo(photo=welcome_image, caption=welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.reply_photo(photo=welcome_image, caption=welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def start_sell_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        if update.message:
            await update.message.reply_text("⚠️ Item submissions using /add can only be done inside Private Chat (DM) with the bot!")
        return ConversationHandler.END

    user_id = update.effective_user.id
    if not submission_open and not is_admin_or_owner(user_id):
        await update.message.reply_text("⛔ Item submissions are currently CLOSED by the Owner.")
        return ConversationHandler.END

    if not is_admin_or_owner(user_id) and user_id not in verified_users:
        await update.message.reply_text("⚠️ You must be a Verified User to list items for auction! Ask an Admin or Owner to verify you.")
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["hexa_texts"] = []
    context.user_data["photo_id"] = None
    keyboard = [
        [InlineKeyboardButton("👑 Legendaries", callback_data="Legendary"),
         InlineKeyboardButton("⚔️ Non-Legendaries", callback_data="Non-Legendary")],
        [InlineKeyboardButton("✨ Shiny", callback_data="Shiny"),
         InlineKeyboardButton("💿 TMs", callback_data="TM")]
    ]
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text("Select Auction Category:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("Select Auction Category:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CATEGORY

async def start_sell_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start_sell_cmd(update, context)

async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data
    context.user_data["category"] = category
    done_btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done Forwarding", callback_data="done_media")]])
    if category == "TM":
        context.user_data["name"] = "TM"
        await query.edit_message_text("Forward the TM info message from Hexa bot, then click '✅ Done Forwarding' below:", reply_markup=done_btn)
        return COLLECT_MEDIA
    else:
        await query.edit_message_text(f"Selected: {category}\n\nEnter exact name of the Pokémon:")
        return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    done_btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done Forwarding", callback_data="done_media")]])
    await update.message.reply_text("Forward Hexa bot pages one by one:\n1. Nature page\n2. IVs/EVs page\n3. Moveset page\n\n👉 Forward them now, and click '✅ Done Forwarding' when finished:", reply_markup=done_btn)
    return COLLECT_MEDIA

async def collect_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.photo:
        context.user_data["photo_id"] = msg.photo[-1].file_id
    
    txt = msg.caption or msg.text or ""
    if txt:
        context.user_data["hexa_texts"].append(txt)
        count = len(context.user_data["hexa_texts"])
        await msg.reply_text(f"📥 Page details saved! ({count} total). Forward more, or click '✅ Done Forwarding' when finished.")
    else:
        await msg.reply_text("⚠️ Message received! Forward more pages or click '✅ Done Forwarding'.")
    return COLLECT_MEDIA

async def finish_media_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not context.user_data.get("hexa_texts") and not context.user_data.get("photo_id"):
        await query.message.reply_text("❌ You haven't forwarded any messages yet! Please forward the Hexa bot messages first.")
        return COLLECT_MEDIA

    if context.user_data["category"] == "TM":
        context.user_data["boosted"] = "N/A"
        await query.message.reply_text("✅ TM info saved!\n\nEnter the Base Price (e.g. 3000, 3k, 1.5k):")
        return BASE_PRICE
    else:
        kb = [[InlineKeyboardButton("Yes", callback_data="yes"), InlineKeyboardButton("No", callback_data="no")]]
        await query.message.reply_text("✅ Pages saved successfully!\n\nAre the Pokémon IVs boosted?", reply_markup=InlineKeyboardMarkup(kb))
        return BOOSTED_ASK

async def get_boosted_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "yes":
        await query.edit_message_text("Please specify which IVs are boosted (e.g. Atk, Spe):")
        return BOOSTED_SPECIFY
    else:
        context.user_data["boosted"] = "No"
        await query.message.reply_text("Enter the Base Price (e.g. 3000, 3k, 1.5k):")
        return BASE_PRICE

async def invalid_boosted_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Yes", callback_data="yes"), InlineKeyboardButton("No", callback_data="no")]]
    await update.message.reply_text("⚠️ Please click either Yes or No using the buttons above regarding boosted IVs.", reply_markup=InlineKeyboardMarkup(kb))
    return BOOSTED_ASK

async def get_boosted_specify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["boosted"] = update.message.text.strip()
    await update.message.reply_text("Enter the Base Price (e.g. 3000, 3k, 1.5k):")
    return BASE_PRICE

def format_item_card(d, user, item_id="Preview", current_bid_display=None):
    category_header = f"▎ {d['category'].upper()} ❞\n"
    poke_name = d.get('name', 'N/A')
    name_line = f"▎ Pokémon Name - {poke_name}\n" if d['category'] != "TM" else ""
    hexa_details = "\n\n".join(d.get("hexa_texts", []))
    
    user_name = user.first_name if hasattr(user, 'first_name') else "User"
    username = f"@{user.username}" if hasattr(user, 'username') and user.username else "N/A"
    base_int = parse_price(d.get('base_price', 0))
    
    if d['category'] == "TM":
        user_block = (
            f"▎ User - {user_name} ❞\n"
            f"▎ Username- {username}\n"
            f"▎ Base - {base_int:,}"
        )
    else:
        user_block = (
            f"▎ User - {user_name} ❞\n"
            f"▎ Username- {username}\n"
            f"▎ Boosted - {d.get('boosted', 'No')}\n"
            f"▎ Base - {base_int:,}"
        )
    
    if current_bid_display:
        user_block += f"\n▎ Current Bid - {current_bid_display}"
    else:
        user_block += f"\n▎ Current Bid - None (No bids yet)"

    card = f"{category_header}\n{name_line}{hexa_details}\n\n{user_block}\n\nItem ID - {item_id}"
    return card

async def get_base_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw = update.message.text.strip()
        parsed_val = parse_price(raw)
        context.user_data["base_price_raw"] = raw
        context.user_data["base_price"] = parsed_val

        d = context.user_data
        user = update.effective_user
        card_text = format_item_card(d, user, item_id="Preview")
        kb = [[InlineKeyboardButton("✅ Confirm & Submit", callback_data="submit"), InlineKeyboardButton("🔄 Restart", callback_data="restart")]]
        
        if d.get("photo_id"):
            await update.message.reply_photo(photo=d["photo_id"], caption=card_text, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(text=card_text, reply_markup=InlineKeyboardMarkup(kb))
        return CONFIRMATION
    except Exception as e:
        await update.message.reply_text("❌ Invalid price format. Please enter a valid base price (e.g. 3000, 3k, 1.5k):")
        return BASE_PRICE

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "restart":
        await query.edit_message_text("Cancelled. Send /start or /add to restart.")
        return ConversationHandler.END

    item_id = generate_item_id()
    d = dict(context.user_data)
    user = query.from_user
    d["seller_id"] = user.id
    d["seller_username"] = f"@{user.username}" if user.username else user.first_name
    d["status"] = "pending"
    auction_db[item_id] = d
    save_data()

    await query.message.reply_text(f"Successfully Submitted! Item ID: {item_id}. Awaiting admin approval.")

    admin_card = format_item_card(d, user, item_id=item_id)
    admin_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"adm_app_{item_id}"), InlineKeyboardButton("Reject", callback_data=f"adm_rej_{item_id}")]])

    if d.get("photo_id"):
        await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=d["photo_id"], caption=admin_card, reply_markup=admin_kb)
    else:
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_card, reply_markup=admin_kb)
    return ConversationHandler.END

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    
    if not is_admin_or_owner(user.id):
        await query.answer("❌ Only Admins or Owner can approve/reject items!", show_alert=True)
        return

    data = query.data
    admin_mention = f"@{user.username}" if user.username else user.first_name

    if data.startswith("adm_app_"):
        await query.answer()
        item_id = data.split("_")[2]
        item = auction_db.get(item_id)
        if not item:
            await query.edit_message_text("❌ Item not found in database.")
            return

        if item.get("status") == "approved":
            await query.answer("⚠️ Item has already been approved!", show_alert=True)
            return

        item["status"] = "approved"
        item["current_bid"] = None
        item["highest_bidder"] = None

        seller_user = await context.bot.get_chat(item["seller_id"])
        chan_card = format_item_card(item, seller_user, item_id=item_id, current_bid_display=None)

        if item.get("photo_id"):
            header = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=item["photo_id"], caption=chan_card)
        else:
            header = await context.bot.send_message(chat_id=CHANNEL_ID, text=chan_card)
            
        item["channel_msg_id"] = header.message_id
        item["has_photo"] = bool(item.get("photo_id"))
        save_data()

        orig_caption = query.message.caption or query.message.text or ""
        approved_text = f"{orig_caption}\n\n✅ Approved by {admin_mention}"
        
        if query.message.photo:
            await query.edit_message_caption(caption=approved_text, reply_markup=None)
        else:
            await query.edit_message_text(text=approved_text, reply_markup=None)

        await context.bot.send_message(chat_id=item["seller_id"], text=f"🎉 Your item {item_id} has been approved and published to the channel!")

    elif data.startswith("adm_rej_"):
        await query.answer()
        item_id = data.split("_")[2]
        item = auction_db.get(item_id)
        if not item:
            await query.edit_message_text("❌ Item not found in database.")
            return

        pending_rejections[user.id] = {
            "item_id": item_id,
            "admin_mention": admin_mention,
            "message": query.message
        }
        await query.message.reply_text(f"❓ Admin {admin_mention}, please type the reason for rejecting item `{item_id}`:")

async def handle_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in pending_rejections:
        return

    rej_data = pending_rejections.pop(user_id)
    item_id = rej_data["item_id"]
    admin_mention = rej_data["admin_mention"]
    adm_msg = rej_data["message"]
    reason = update.message.text.strip()

    item = auction_db.get(item_id)
    if item:
        item["status"] = "rejected"
        save_data()
        
        orig_text = adm_msg.caption or adm_msg.text or ""
        rejected_card = f"{orig_text}\n\n❌ Rejected by {admin_mention}\nReason: {reason}"

        if adm_msg.photo:
            await adm_msg.edit_caption(caption=rejected_card, reply_markup=None)
        else:
            await adm_msg.edit_text(text=rejected_card, reply_markup=None)

        seller_text = f"❌ Your auction item ({item.get('name', 'Item')}) [ID: {item_id}] was rejected by {admin_mention}.\n\n📌 Reason: {reason}"
        try:
            await context.bot.send_message(chat_id=item["seller_id"], text=seller_text)
        except Exception as e:
            logging.error(f"Failed to DM seller: {e}")

    await update.message.reply_text(f"✅ Rejection reason sent to seller for Item ID `{item_id}`.")

async def place_bid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    if not auction_started:
        await update.message.reply_text("⛔ Bidding is currently CLOSED by the Owner.")
        return

    if not is_admin_or_owner(user_id) and user_id not in verified_users:
        await update.message.reply_text("⚠️ You must be a Verified User to place bids! Request an Admin to verify you.")
        return

    text = update.message.text.strip()
    parts = text.split()

    bid_raw = None
    if len(parts) > 1:
        bid_raw = parts[1]
    elif context.args:
        bid_raw = context.args[0]

    if not bid_raw:
        await update.message.reply_text("Usage: Reply to an item card with /bid <amount> (e.g. /bid 4k)")
        return

    item_id = None
    if update.message.reply_to_message:
        replied = update.message.reply_to_message
        replied_text = replied.text or replied.caption or ""
        match = re.search(r"Item\s*ID\s*[:-]?\s*([a-zA-Z0-9]+)", replied_text, re.IGNORECASE)
        if match:
            item_id = match.group(1)

    if not item_id and auction_db:
        item_id = list(auction_db.keys())[-1]

    if not item_id:
        await update.message.reply_text("❌ No active auction item found to bid on!")
        return

    item = auction_db.get(item_id)
    if not item:
        await update.message.reply_text(f"❌ Invalid Item ID ({item_id}).")
        return

    if item.get("seller_id") == user_id:
        await update.message.reply_text("❌ You cannot bid on your own item!")
        return

    try:
        bid_val = parse_price(bid_raw)
    except ValueError:
        await update.message.reply_text("❌ Invalid bid format. Use numbers or 'k' notation (e.g. 4k, 5000, 4.5k).")
        return

    base_price = parse_price(item["base_price"])
    curr_bid_val = item.get("current_bid")

    if curr_bid_val is None:
        min_bid_required = base_price
    else:
        min_bid_required = parse_price(curr_bid_val) + 1000

    if bid_val < min_bid_required:
        curr_text = f"{parse_price(curr_bid_val):,}" if curr_bid_val is not None else f"{base_price:,} (No bids yet)"
        await update.message.reply_text(
            f"❌ Bid too low!\n\n"
            f"• Base Price: {base_price:,}\n"
            f"• Current Bid: {curr_text}\n"
            f"👉 Minimum valid bid: {min_bid_required:,}"
        )
        return

    item["current_bid"] = bid_val
    bidder_name = update.effective_user.first_name
    bidder_username = f"@{update.effective_user.username}" if update.effective_user.username else bidder_name
    item["highest_bidder"] = bidder_username
    save_data()

    await update.message.reply_text(
        f"✅ Bid Placed Successfully!\n\n"
        f"• Item ID: {item_id}\n"
        f"• Current Highest Bid: {bid_val:,}\n"
        f"• Highest Bidder: {bidder_username}"
    )

    try:
        seller_user = await context.bot.get_chat(item["seller_id"])
        display_str = f"{bid_val:,} ({bidder_username})"
        chan_card = format_item_card(item, seller_user, item_id=item_id, current_bid_display=display_str)

        if item.get("has_photo"):
            await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=item["channel_msg_id"], caption=chan_card)
        else:
            await context.bot.edit_message_text(chat_id=CHANNEL_ID, message_id=item["channel_msg_id"], text=chan_card)
    except Exception as e:
        logging.error(f"Channel card edit error: {e}")

async def my_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_items = [
        (item_id, item) for item_id, item in auction_db.items() 
        if item.get("seller_id") == user_id and item.get("status") == "approved"
    ]

    if not user_items:
        await update.message.reply_text("ℹ️ You have no active approved items in the auction.")
        return

    clean_channel = CHANNEL_ID.replace("@", "")
    lines = ["📦 **Your Active Auction Items:**\n"]
    for item_id, item in user_items:
        name = item.get("name", "TM" if item.get("category") == "TM" else "Item")
        msg_id = item.get("channel_msg_id")
        link = f"https://t.me/{clean_channel}/{msg_id}" if msg_id else MAIN_GC_LINK
        curr_bid = item.get("current_bid")
        bid_str = f"{parse_price(curr_bid):,}" if curr_bid else "No bids yet"
        lines.append(f"• [{name} (ID: {item_id})]({link}) — Current Bid: {bid_str}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)

async def my_bids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = f"@{user.username}" if user.username else user.first_name

    user_bids = [
        (item_id, item) for item_id, item in auction_db.items()
        if item.get("highest_bidder") == username and item.get("status") == "approved"
    ]

    if not user_bids:
        await update.message.reply_text("ℹ️ You are currently not the highest bidder on any active items.")
        return

    clean_channel = CHANNEL_ID.replace("@", "")
    lines = ["💰 **Items Where You Hold Highest Bid:**\n"]
    for item_id, item in user_bids:
        name = item.get("name", "TM" if item.get("category") == "TM" else "Item")
        msg_id = item.get("channel_msg_id")
        link = f"https://t.me/{clean_channel}/{msg_id}" if msg_id else MAIN_GC_LINK
        curr_bid = parse_price(item.get("current_bid", 0))
        lines.append(f"• [{name} (ID: {item_id})]({link}) — Your Bid: {curr_bid:,}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)

async def all_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_items = [
        (item_id, item) for item_id, item in auction_db.items()
        if item.get("status") == "approved"
    ]

    if not active_items:
        await update.message.reply_text("ℹ️ There are currently no active items listed in the auction.")
        return

    clean_channel = CHANNEL_ID.replace("@", "")
    lines = ["🌐 **All Active Auction Items:**\n"]
    for item_id, item in active_items:
        name = item.get("name", "TM" if item.get("category") == "TM" else "Item")
        msg_id = item.get("channel_msg_id")
        link = f"https://t.me/{clean_channel}/{msg_id}" if msg_id else MAIN_GC_LINK
        curr_bid = item.get("current_bid")
        bid_str = f"{parse_price(curr_bid):,}" if curr_bid else "No bids"
        lines.append(f"• [{name} (ID: {item_id})]({link}) | Bid: {bid_str}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)

async def verify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_or_owner(update.effective_user.id):
        await update.message.reply_text("❌ Only Admins or Owner can verify users.")
        return

    target_id = await resolve_target_user_id(update, context)
    if not target_id:
        await update.message.reply_text("Usage: Reply to a user with /verify or use /verify <user_id> / /verify @username.")
        return

    verified_users.add(target_id)
    save_data()
    await update.message.reply_text(f"✅ User {target_id} has been VERIFIED to participate in auctions!")

async def unverify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_or_owner(update.effective_user.id):
        await update.message.reply_text("❌ Only Admins or Owner can unverify users.")
        return

    target_id = await resolve_target_user_id(update, context)
    if not target_id:
        await update.message.reply_text("Usage: Reply to a user with /unverify or use /unverify <user_id> / /unverify @username.")
        return

    verified_users.discard(target_id)
    save_data()
    await update.message.reply_text(f"❌ User {target_id} is now UNVERIFIED.")

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text(f"👑 Only the Owner ({OWNER_ID}) can promote bot admins!")
        return

    target_id = await resolve_target_user_id(update, context)
    if not target_id:
        await update.message.reply_text("Usage: Reply to a user with /addadmin or use /addadmin <user_id> / /addadmin @username.")
        return

    bot_admins.add(target_id)
    save_data()
    await update.message.reply_text(f"⭐ User {target_id} is now a Bot Admin!")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("👑 Only the Owner can demote bot admins!")
        return

    target_id = await resolve_target_user_id(update, context)
    if not target_id:
        await update.message.reply_text("Usage: Reply to a user with /removeadmin or use /removeadmin <user_id> / /removeadmin @username.")
        return

    bot_admins.discard(target_id)
    save_data()
    await update.message.reply_text(f"🔻 User {target_id} has been demoted from Bot Admin.")

async def toggle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global submission_open
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("👑 Only the Owner can open/close submissions!")
        return

    cmd = update.message.text.split()[0].lower()
    if "open" in cmd:
        submission_open = True
        await update.message.reply_text("🟢 Item Submissions are now OPEN!")
    else:
        submission_open = False
        await update.message.reply_text("🔴 Item Submissions are now CLOSED!")

async def toggle_auction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auction_started
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("👑 Only the Owner can start/end the auction!")
        return

    cmd = update.message.text.split()[0].lower()
    if "start" in cmd:
        auction_started = True
        await update.message.reply_text("🟢 Auction Bidding is now LIVE!")
    else:
        auction_started = False
        await update.message.reply_text("🔴 Auction Bidding has ENDED!")

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_owner(user_id):
        await update.message.reply_text("❌ You do not have permission to view admin controls.")
        return

    role = "👑 Owner" if is_owner(user_id) else "⭐ Admin"
    help_text = (
        f"🛠️ PW Auction Bot — Control Panel ({role})\n\n"
        "Admin & Owner Commands:\n"
        "• /verify — Reply to a user or pass ID/username to verify them\n"
        "• /unverify — Revoke user verification\n\n"
    )
    if is_owner(user_id):
        help_text += (
            "👑 Owner Special Controls:\n"
            "• /addadmin — Promote user by reply, ID, or @username\n"
            "• /removeadmin — Demote user by reply, ID, or @username\n"
            "• /opensell — Open item submissions\n"
            "• /closesell — Close item submissions\n"
            "• /startauction — Start bidding window\n"
            "• /endauction — End bidding window\n"
        )
    await update.message.reply_text(help_text)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))
    app.add_handler(CommandHandler("verify", verify_user))
    app.add_handler(CommandHandler("unverify", unverify_user))
    app.add_handler(CommandHandler("admin", admin_help))

    app.add_handler(CommandHandler("myitems", my_items))
    app.add_handler(CommandHandler("mybids", my_bids))
    app.add_handler(CommandHandler("allitems", all_items))

    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(handle_confirmation, pattern="^(submit|restart)$"))

    conv = ConversationHandler(
        entry_points=[CommandHandler("add", start_sell_cmd), CallbackQueryHandler(start_sell_callback, pattern="^start_sell$")],
        states={
            CATEGORY: [CallbackQueryHandler(category_selected)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            COLLECT_MEDIA: [MessageHandler(filters.ALL & ~filters.COMMAND, collect_media), CallbackQueryHandler(finish_media_collection, pattern="^done_media$")],
            BOOSTED_ASK: [
                CallbackQueryHandler(get_boosted_ask),
                MessageHandler(filters.TEXT & ~filters.COMMAND, invalid_boosted_ask)
            ],
            BOOSTED_SPECIFY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_boosted_specify)],
            BASE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_base_price)],
            CONFIRMATION: [CallbackQueryHandler(handle_confirmation, pattern="^(submit|restart)$")]
        },
        fallbacks=[CommandHandler("add", start_sell_cmd)],
        per_message=False
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    
    app.add_handler(CommandHandler("bid", place_bid))
    app.add_handler(CommandHandler("bud", place_bid))

    app.add_handler(CommandHandler(["opensell", "closesell"], toggle_submission))
    app.add_handler(CommandHandler(["startauction", "endauction"], toggle_auction))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reject_reason))

    # Start the Flask web server in a background thread so Render's port checker passes
    Thread(target=run_web, daemon=True).start()

    # Start Telegram bot polling
    app.run_polling()

if __name__ == "__main__":
    main()
