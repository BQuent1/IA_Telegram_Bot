import os
import logging
import feedparser
import requests
import asyncio
import sys
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- 1. CONFIGURATION & ENVIRONNEMENT ---
load_dotenv()
# Utilisation de la clé OpenRouter fournie
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_KEY"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 2. UTILITAIRES LLM ---
async def get_llm_response(prompt):
    """Appel simple pour les Bots 1 et 2"""
    response = client.chat.completions.create(
        model="google/gemini-2.0-flash-001",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

async def get_llm_response_from_history(messages):
    """Appel avec historique pour le Bot 3"""
    response = client.chat.completions.create(
        model="google/gemini-2.0-flash-001",
        messages=messages
    )
    return response.choices[0].message.content

# --- 3. GESTION DES DOCUMENTS (.md) ---
async def set_document_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Définit quel document l'utilisateur s'apprête à envoyer"""
    cmd = update.message.text.split()[0][4:] # Récupère 'cv', 'fiche' ou 'interviewer'
    context.user_data['waiting_for'] = cmd
    await update.message.reply_text(f"Envoie maintenant le fichier .md pour : {cmd.upper()}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Télécharge et stocke le contenu du fichier Markdown"""
    doc_type = context.user_data.get('waiting_for')
    if not doc_type:
        await update.message.reply_text("Utilise d'abord /setcv, /setfiche ou /setinterviewer")
        return

    try:
        file = await context.bot.get_file(update.message.document.file_id)
        content = await file.download_as_bytearray()
        # Stockage dans context.user_data pour isoler par utilisateur
        context.user_data[doc_type] = content.decode("utf-8")
        context.user_data['waiting_for'] = None
        await update.message.reply_text(f"✅ {doc_type.upper()} bien reçu et enregistré !")
    except Exception as e:
        await update.message.reply_text(f"Erreur lors de la lecture du fichier : {e}")

# --- 4. LOGIQUE DES BOTS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bienvenue ! Voici tes commandes :\n"
        "1️⃣ Config : /setcv, /setfiche, /setinterviewer\n"
        "2️⃣ Bot 1 (Sourceur) : /parse\n"
        "3️⃣ Bot 2 (Coach CV) : /cv\n"
        "4️⃣ Bot 3 (Entretien) : /entretien puis /stop"
    )

async def parse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot 1 : Matching via Flux RSS"""
    cv = context.user_data.get('cv')
    if not cv:
        await update.message.reply_text("❌ Envoie ton CV d'abord avec /setcv")
        return

    await update.message.reply_text("🔍 Analyse du flux RSS en cours...")
    feed = feedparser.parse(os.getenv("RSS_URL"))
    
    # Mode démo si le flux est vide
    if not feed.entries:
        await update.message.reply_text("⚠️ Flux vide, utilisation d'offres de test.")
        jobs = "1. Dev IA chez TechCorp (Paris)\n2. Data Analyst chez DataSoft (Lyon)"
    else:
        jobs = "\n".join([f"- {e.title}: {e.link}" for e in feed.entries[:5]])

    prompt = f"Voici mon CV :\n{cv}\n\nVoici des offres :\n{jobs}\n\nSélectionne les 2 meilleures et justifie."
    res = await get_llm_response(prompt)
    await update.message.reply_text(res)

async def cv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot 2 : Analyse comparative CV/Fiche"""
    cv, fiche = context.user_data.get('cv'), context.user_data.get('fiche')
    if not cv or not fiche:
        await update.message.reply_text("❌ Il me manque le CV ou la Fiche de poste.")
        return

    await update.message.reply_text("📊 Comparaison détaillée en cours...")
    prompt = f"Analyse le match entre ce CV :\n{cv}\n\net cette offre :\n{fiche}\n\nDonne un score sur 100 et des conseils de reformulation."
    res = await get_llm_response(prompt)
    await update.message.reply_text(res)

async def entretien_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot 3 : Initialisation de la simulation"""
    cv = context.user_data.get('cv')
    fiche = context.user_data.get('fiche')
    interviewer = context.user_data.get('interviewer')

    if not all([cv, fiche, interviewer]):
        await update.message.reply_text("❌ Documents manquants (CV, Fiche ou Interviewer).")
        return

    await update.message.reply_text("🎤 L'entretien commence. Le recruteur se prépare...")

    # Création du persona et de l'historique
    context.user_data['messages'] = [
        {"role": "system", "content": f"Tu es {interviewer}. Tu recrutes pour : {fiche}. Le candidat est : {cv}. Pose une question à la fois. Ajoute toujours un feedback entre crochets [COACH: ...]."}
    ]
    context.user_data['messages'].append({"role": "user", "content": "Bonjour, je suis prêt pour l'entretien."})
    
    try:
        response = await get_llm_response_from_history(context.user_data['messages'])
        context.user_data['messages'].append({"role": "assistant", "content": response})
        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"Erreur IA : {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot 3 : Boucle de conversation pour l'entretien"""
    if 'messages' not in context.user_data:
        return # On ignore si l'entretien n'a pas démarré

    user_text = update.message.text
    context.user_data['messages'].append({"role": "user", "content": user_text})

    try:
        response = await get_llm_response_from_history(context.user_data['messages'])
        context.user_data['messages'].append({"role": "assistant", "content": response})
        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"Erreur IA : {e}")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot 3 : Bilan final"""
    if 'messages' not in context.user_data:
        return

    await update.message.reply_text("🏁 Entretien terminé. Génération du bilan...")
    history = str(context.user_data['messages'])
    bilan = await get_llm_response(f"Fais un bilan critique de cet entretien (points forts/faibles) : {history}")
    await update.message.reply_text(bilan)
    del context.user_data['messages'] # Nettoyage de la session

# --- 5. LANCEMENT DU BOT ---
if __name__ == '__main__':
    # Correctif pour éviter les erreurs asyncio sur Windows
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Handlers Fichiers
    for cmd in ['setcv', 'setfiche', 'setinterviewer']:
        app.add_handler(CommandHandler(cmd, set_document_type))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Handlers Commandes
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("parse", parse_command))
    app.add_handler(CommandHandler("cv", cv_command))
    app.add_handler(CommandHandler("entretien", entretien_command))
    app.add_handler(CommandHandler("stop", stop_command))
    
    # Handler Texte (doit être en dernier)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Bot en ligne ! (CTRL+C pour arrêter)")
    app.run_polling()