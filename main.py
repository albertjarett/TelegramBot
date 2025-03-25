import logging
import sqlite3
import hashlib
from io import BytesIO
from PIL import Image
import imagehash
from telegram import Update
from telegram.ext import Application, MessageHandler, filters
import pytesseract
from datetime import datetime
import re

# Configuración
TOKEN = "7910942735:AAHVLnkTrljFdxIktYCBzD0rJPlby4RGSiE"
DB_NAME = "comprobantes.db"

# Expresiones regulares precompiladas
BANCO_REGEX = re.compile(
    r'\b(banco|bancamiga|banamex|bbva|santander|hsbc|banorte|scotiabank|banregio|banbajio|inbursa|azteca|bancoppel)\b',
    re.IGNORECASE)
MONTO_REGEX = re.compile(r'\$\s*(\d{1,3}(?:,\d{3})*\.\d{2})')
FECHA_REGEX = re.compile(r'\b(\d{2}/\d{2}/\d{4})\b')
REFERENCIA_REGEX = re.compile(r'\b([A-Z0-9]{10,20})\b')

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)


def init_db():
    """Inicializa la base de datos"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS comprobantes (
                phash TEXT PRIMARY KEY,
                metadata_hash TEXT,
                fecha TIMESTAMP,
                banco TEXT,
                monto REAL,
                fecha_comprobante TEXT,
                referencia TEXT,
                nombre_archivo TEXT
            )
        ''')
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_metadata ON comprobantes(metadata_hash)'
        )
        conn.commit()


init_db()


def generar_phash_imagehash(file_data: bytes) -> str:
    """Genera un hash perceptual usando imagehash"""
    try:
        img = Image.open(BytesIO(file_data))
        phash = str(imagehash.phash(img))
        return phash
    except Exception as e:
        logger.error(f"Error generando phash: {e}")
        raise ValueError("Error generando hash de imagen")


def extraer_texto(file_data: bytes) -> str:
    """Extrae texto de la imagen usando OCR"""
    try:
        img = Image.open(BytesIO(file_data))
        return pytesseract.image_to_string(img, lang='spa').lower()
    except Exception as e:
        logger.error(f"Error en OCR: {e}")
        return ""


def parsear_datos(texto: str) -> dict:
    """Extrae los datos relevantes del texto"""
    datos = {
        'banco': None,
        'monto': None,
        'fecha_comprobante': None,
        'referencia': None
    }

    if (banco_match := BANCO_REGEX.search(texto)):
        datos['banco'] = banco_match.group().lower()

    if (montos := MONTO_REGEX.findall(texto)):
        try:
            datos['monto'] = float(montos[-1].replace(',', ''))
        except ValueError:
            pass

    if (fechas := FECHA_REGEX.findall(texto)):
        datos['fecha_comprobante'] = fechas[-1]

    if (referencias := REFERENCIA_REGEX.findall(texto)):
        datos['referencia'] = referencias[-1]

    return datos


def verificar_duplicado(phash: str, metadata_hash: str) -> bool:
    """Verifica si el comprobante ya está registrado"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT 1 FROM comprobantes 
                WHERE phash = ? OR metadata_hash = ? 
                LIMIT 1
            ''', (phash, metadata_hash))
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Error verificando duplicado: {e}")
        return False


def registrar_comprobante(file_data: bytes, nombre_archivo: str):
    """Procesa y registra un comprobante"""
    try:
        phash = generar_phash_imagehash(file_data)
        texto = extraer_texto(file_data)
        datos = parsear_datos(texto)

        metadata_str = f"{datos['banco']}{datos['monto']}{datos['fecha_comprobante']}{nombre_archivo}"
        metadata_hash = hashlib.md5(metadata_str.encode()).hexdigest()

        if verificar_duplicado(phash, metadata_hash):
            raise ValueError("🔄 Comprobante duplicado detectado")

        with sqlite3.connect(DB_NAME) as conn:
            conn.execute(
                '''
                INSERT INTO comprobantes 
                VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?)
            ''', (phash, metadata_hash, datos['banco'], datos['monto'],
                  datos['fecha_comprobante'], datos['referencia'],
                  nombre_archivo))
            conn.commit()

    except sqlite3.IntegrityError:
        raise ValueError("🔄 Comprobante duplicado (registro simultáneo)")
    except ValueError as ve:
        raise ve
    except Exception as e:
        logger.error(f"Error inesperado registrando comprobante: {e}")
        raise ValueError("❌ Error inesperado al registrar el comprobante")


async def manejar_comprobante(update: Update, context):
    """Manejador principal del bot para registrar comprobantes"""
    try:
        file = update.message.document or update.message.photo[-1]
        nombre_archivo = getattr(
            file, 'file_name',
            f"comp_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg")

        file_data = await (await
                           context.bot.get_file(file.file_id
                                                )).download_as_bytearray()

        registrar_comprobante(file_data, nombre_archivo)

    except ValueError as e:
        await update.message.reply_text(f"⚠️ {str(e)}")
    except Exception as e:
        logger.error(f"Error crítico: {e}", exc_info=True)
        await update.message.reply_text("⏳ Error temporal. Intenta nuevamente."
                                        )


def main():
    """Configura y ejecuta el bot"""
    application = Application.builder().token(TOKEN).build()
    application.add_handler(
        MessageHandler(filters.Document.IMAGE | filters.PHOTO,
                       manejar_comprobante))

    application.run_polling()


if __name__ == '__main__':
    main()
