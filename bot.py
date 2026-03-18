import os
import logging
import feedparser
import requests
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION ---
load_dotenv()
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_KEY"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- UTILS ---
async def get_llm_response(prompt):
    response = client.chat.completions.create(
        model="google/gemini-2.0-flash-001",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

# --- GESTION DES FICHIERS ---
async def set_document_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split()[0][4:] # Récupère 'cv', 'fiche' ou 'interviewer'
    context.user_data['waiting_for'] = cmd
    await update.message.reply_text(f"Envoie maintenant le fichier .md pour : {cmd}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc_type = context.user_data.get('waiting_for')
    if not doc_type:
        await update.message.reply_text("Utilise d'abord /setcv, /setfiche ou /setinterviewer")
        return

    file = await context.bot.get_file(update.message.document.file_id)
    content = await file.download_as_bytearray()
    context.user_data[doc_type] = content.decode("utf-8")
    context.user_data['waiting_for'] = None
    await update.message.reply_text(f"✅ {doc_type.upper()} enregistré !")

# --- COMMANDES DES BOTS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot prêt. Commandes : /setcv, /setfiche, /setinterviewer, /parse, /cv, /entretien /stop")

async def parse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. Vérifier si on a le CV en mémoire
    cv_content = context.user_data.get('cv')
    if not cv_content:
        await update.message.reply_text("❌ Erreur : Je n'ai pas ton CV. Envoie /setcv puis ton fichier .md d'abord.")
        return

    await update.message.reply_text("🔍 Je fouille le flux RSS pour trouver des pépites...")

    # 2. Récupérer le flux RSS
    rss_url = os.getenv("RSS_URL")
    feed = feedparser.parse(rss_url)
    
    # --- MODE DÉMO SI FLUX VIDE ---
    if not feed.entries:
        await update.message.reply_text("⚠️ Flux RSS vide, passage en mode démo avec des offres fictives...")
        job_list = [
            "Titre: Stagiaire Data Scientist - Google Paris\nLien: https://google.com\nDescription: Nous cherchons un étudiant de l'ESIEE sachant coder en Python et intéressé par les LLMs.\n---",
            "Titre: Développeur Web Junior - Ubisoft\nLien: https://ubisoft.com\nDescription: Stage en C++ et Python pour du rendu graphique en temps réel.\n---",
            "Titre: Expert Excel - Cabinet Comptable Martin\nLien: https://martin.fr\nDescription: Saisie de données comptables sur tableur toute la journée.\n---"
        ]
    else:
        # On prépare la liste à partir du vrai flux
        job_list = []
        for entry in feed.entries[:5]:
            job_list.append(f"Titre: {entry.title}\nLien: {entry.link}\nDescription: {entry.summary}\n---")
    # ------------------------------

    jobs_text = "\n".join(job_list)

    # On prépare une liste simplifiée des 5 dernières offres
    job_list = []
    for entry in feed.entries[:5]: # On limite à 5 pour ne pas surcharger l'IA
        job_list.append(f"Titre: {entry.title}\nLien: {entry.link}\nDescription: {entry.summary}\n---")

    jobs_text = "\n".join(job_list)

    # 3. Le Prompt pour l'IA
    prompt = f"""
    Tu es un expert en recrutement. Voici mon CV en Markdown :
    {cv_content}

    Voici les dernières offres de stages trouvées :
    {jobs_text}

    Analyse ces offres. Sélectionne les 2 ou 3 meilleures pour moi.
    Pour chaque offre sélectionnée, donne-moi :
    1. Le titre et le lien.
    2. Pourquoi c'est un bon match (justification).
    3. Un conseil rapide pour adapter mon CV à cette offre.
    
    Réponds de manière concise et encourageante en Markdown.
    """

    # 4. Appel à OpenRouter
    try:
        analyse = await get_llm_response(prompt)
        
        try:
            # On tente l'affichage joli en Markdown
            await update.message.reply_text(analyse, parse_mode='Markdown')
        except Exception:
            # Si Telegram râle sur le Markdown, on envoie en texte brut
            await update.message.reply_text("⚠️ (Affichage simplifié car le Markdown était mal formé) :\n\n" + analyse)
            
    except Exception as e:
        await update.message.reply_text(f"Oups, l'IA a eu un bug : {e}")

async def cv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cv = context.user_data.get('cv')
    fiche = context.user_data.get('fiche')

    if not cv or not fiche:
        await update.message.reply_text("❌ Il me manque le CV (/setcv) ou la fiche de poste (/setfiche) !")
        return

    await update.message.reply_text("📊 Analyse du 'match' en cours... Prépare-toi, je vais être honnête.")

    prompt = f"""
    Analyse la correspondance entre ce CV et cette offre d'emploi.
    
    CV :
    {cv}
    
    OFFRE :
    {fiche}
    
    Donne un rapport structuré :
    1. Score de match (sur 100).
    2. Points forts (ce qui colle parfaitement).
    3. Gaps critiques (ce qui manque pour être pris).
    4. Reformulations concrètes : Propose 2 phrases de mon CV à modifier pour mieux répondre aux mots-clés de l'offre.
    5. Message LinkedIn : Un court message pour contacter le recruteur.
    """

    try:
        reponse = await get_llm_response(prompt)
        # On utilise le même système de secours pour le Markdown
        try:
            await update.message.reply_text(reponse, parse_mode='Markdown')
        except:
            await update.message.reply_text(reponse)
    except Exception as e:
        await update.message.reply_text(f"Erreur : {e}")

async def entretien_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cv = context.user_data.get('cv')
    fiche = context.user_data.get('fiche')
    interviewer = context.user_data.get('interviewer')

    if not all([cv, fiche, interviewer]):
        await update.message.reply_text("❌ Il me manque des documents (CV, Fiche ou Interviewer).")
        return

    # Initialisation de l'historique avec le "System Prompt"
    context.user_data['messages'] = [
        {"role": "system", "content": f"""
        Tu es {interviewer}. Tu fais passer un entretien pour le poste : {fiche}.
        Le candidat est : {cv}.
        
        CONSIGNES :
        1. Reste dans ton personnage à 100%.
        2. Pose UNE SEULE question à la fois.
        3. Après chaque réponse du candidat, donne ton feedback de coach entre crochets [COACH: ...] puis continue l'entretien.
        4. Sois exigeant mais juste.
        """}
    ]
    
    # Premier message du recruteur
    first_prompt = "Commence l'entretien, présente-toi brièvement et pose la première question."
    context.user_data['messages'].append({"role": "user", "content": first_prompt})
    
    response = await get_llm_response_from_history(context.user_data['messages'])
    context.user_data['messages'].append({"role": "assistant", "content": response})
    
    await update.message.reply_text(response)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Si on n'est pas en mode entretien, on ignore
    if 'messages' not in context.user_data:
        await update.message.reply_text("Tape /entretien pour commencer une simulation.")
        return

    user_answer = update.message.text
    context.user_data['messages'].append({"role": "user", "content": user_answer})

    # On récupère la suite de l'entretien
    response = await get_llm_response_from_history(context.user_data['messages'])
    context.user_data['messages'].append({"role": "assistant", "content": response})

    await update.message.reply_text(response)

# Nouvelle utilitaire pour gérer l'historique
async def get_llm_response_from_history(messages):
    response = client.chat.completions.create(
        model="google/gemini-2.0-flash-001",
        messages=messages
    )
    return response.choices[0].message.content


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'messages' not in context.user_data:
        return

    await update.message.reply_text("🏁 Fin de l'entretien. Calcul du bilan final...")
    
    history = str(context.user_data['messages'])
    prompt = f"Analyse cet historique d'entretien : {history}. Donne une note sur 10, les points forts, et 3 axes d'amélioration."
    
    bilan = await get_llm_response(prompt)
    await update.message.reply_text(bilan)
    
    # Reset de la session
    del context.user_data['messages']

# --- MAIN ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Handlers fichiers
    for cmd in ['setcv', 'setfiche', 'setinterviewer']:
        app.add_handler(CommandHandler(cmd, set_document_type))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Handlers actions
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("parse", parse_command))
    app.add_handler(CommandHandler("cv", cv_command))
    app.add_handler(CommandHandler("entretien", entretien_command))
    app.add_handler(CommandHandler("stop", stop_command))
    # Ce handler doit être à la fin pour ne pas intercepter les commandes
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot en ligne...")
    app.run_polling()