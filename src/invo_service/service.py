import os
import time
import redis
import logging
import threading
from pathlib import Path
from lib_ftep import FTEP
from fastapi import FastAPI
from lib_mail import Mailbox
from lib_invoice import Invoice
from lib_idoc.invoice import IDOC
from uvicorn import Config, Server
from lib_utilys import read_json, write_json
from fastapi.middleware.cors import CORSMiddleware
from lib_azure.ai_document_intelligence import FormRecognizer

ftp = FTEP()
mailbox = Mailbox()
form_recognizer = FormRecognizer()

r = redis.Redis(os.getenv('REDIS_HOST'), port=os.getenv('REDIS_PORT'), decode_responses=True)

logging.basicConfig(level=logging.DEBUG)

# PRODUCTION
DATA_ROOT = Path("/data")

# DEVELOPMENT
#HERE = Path(__file__).resolve().parent
#DATA_ROOT = HERE.parent.parent / "data"

CRIT_PATH = DATA_ROOT / "business_subject_criteria.json"
MODELMAP_PATH = DATA_ROOT / "business_models_map.json"
RESULT_PATH = DATA_ROOT / "result.json"
DEBMAP_PATH = DATA_ROOT / "debtor_map.json"
CTRYABBR_PATH = DATA_ROOT / "country_abbreviations_map.json"
EUABBR_PATH = DATA_ROOT / "eu_country_abbreviations_map.json"
TAXMAP_PATH = DATA_ROOT / "tax_qualifier_map.json"
STARTSEG_PATH = DATA_ROOT / "static_segment_start.xml"
DYNSEG_PATH = DATA_ROOT / "dynamic_segment.xml"
ENDSEG_PATH = DATA_ROOT / "static_segment_end.xml"

SLIDER_VALUE = 1
SLIDER_LOCK = threading.Lock()

def get_slider_value():
    """Get the current slider value in a thread-safe manner."""
    with SLIDER_LOCK:
        return SLIDER_VALUE
    
def set_slider_value(value: int):
    """Set the slider value in a thread-safe manner."""
    global SLIDER_VALUE
    with SLIDER_LOCK:
        SLIDER_VALUE = value

def start_mainloop():
    """Start the main processing loop in a separate thread."""
    thread = threading.Thread(target=mainloop)
    thread.start()

app = FastAPI(title="Invoice Processing Queue")

@app.on_event("startup")
async def startup_event():
    """Initialize the mailbox and start the main processing loop."""
    start_mainloop()

if os.getenv('ENV') == 'development':
    app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    )

@app.get("/metadata")
async def list_messages():
    """Return all stored metadata rows from Redis."""
    rows = []
    for uid in r.keys('*'):
        h = r.hgetall(uid)
        if h:
            rows.append({
                'uid': uid,
                'business': h.get('business'),
                'subject': h.get('subject'),
            })
    return rows

@app.post("/slider")
async def update_slider(payload: dict):
    """
    Expects JSON {"value": 1}.
    """
    value = payload.get("value")
    if not isinstance(value, int):
        return {"error": "value must be an integer"}, 400

    set_slider_value(value)
    return {"value": get_slider_value()}

def mainloop():
    while True:
        mailbox.initialize_uid(slider_value=get_slider_value())
        logging.info("Starting main processing loop with slider value: %d", get_slider_value())

        try:
            uids = mailbox.list_uids()
            mailbox.set_metadata_redis(r, uids)
        except Exception:
            logging.exception("Failed to list or extract metadata from emails")
            time.sleep(60)

        for invoice, idoc in mailbox.create_invoice_and_idoc(uids, Invoice, IDOC, STARTSEG_PATH, DYNSEG_PATH, ENDSEG_PATH):
            if mailbox.should_process(CRIT_PATH, invoice):
                logging.info("Processing invoice: %s from %s", invoice.subject, invoice.business)

                # PRODUCTION
                #result = form_recognizer.analyze_document(MODELMAP_PATH, invoice)

                # TESTING
                #write_json(RESULT_PATH, result)
                result = read_json(RESULT_PATH)

                try:
                    result_parsed_numbers = form_recognizer.parse_numbers(result)
                    result_parsed_dates = form_recognizer.parse_dates(result_parsed_numbers)
                    kv_pairs = form_recognizer.extract_kv_pairs(result_parsed_dates)
                except Exception:
                    logging.exception("Failed to parse numbers or dates from result")
                    mailbox.uid = invoice.uid
                    mailbox.flag_email(invoice.uid)
                    continue

                try:
                    invoice.configure_kvpairs(kv_pairs)
                    invoice.additional_kv_pairs(DEBMAP_PATH, CTRYABBR_PATH, TAXMAP_PATH, EUABBR_PATH)
                except Exception:
                    logging.exception("Failed to configure kv pairs for invoice")
                    mailbox.uid = invoice.uid
                    r.delete(invoice.uid)
                    mailbox.flag_email(invoice.uid)
                    continue

                if invoice.type == "NULL":
                    logging.info("Invoice type is NULL, skipping further processing")
                    r.delete(invoice.uid)
                    mailbox.delete_email(invoice.uid)
                    continue

                elif invoice.type == "INVO":
                    idoc.configure_idoc(invoice)
                    try:
                        ftp.connect()
                        #ftp.upload_idoc(idoc)
                        #ftp.upload_pdf(invoice)
                        r.delete(invoice.uid)
                        #mailbox.delete_email(invoice.uid)
                        ftp.disconnect()
                    except Exception:
                        logging.exception("Failed to upload IDOC or PDF to FTP")
                        mailbox.uid = invoice.uid
                        r.delete(invoice.uid)
                        mailbox.flag_email(invoice.uid)
                    continue
                
                elif invoice.type == "CRME":
                    invoice.configure_crme()
                    idoc.configure_idoc(invoice)
                    try:
                        ftp.connect()
                        ftp.upload_idoc(idoc)
                        ftp.upload_pdf(invoice)
                        mailbox.delete_email(invoice.uid)
                        ftp.disconnect()
                    except Exception:
                        logging.exception("Failed to upload IDOC or PDF to FTP")
                        mailbox.uid = invoice.uid
                        r.delete(invoice.uid)
                        mailbox.flag_email(invoice.uid)
                    continue
            else:
                logging.info("Email with subject '%s' does not meet criteria for processing", invoice.subject)
                mailbox.uid = invoice.uid
                r.delete(invoice.uid)
                mailbox.flag_email(invoice.uid)
        
        logging.info("Sleeping for 60 seconds before checking for new emails")
        time.sleep(60)

            

