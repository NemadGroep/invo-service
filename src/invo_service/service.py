import os
import time
import logging
from pathlib import Path
from lib_ftep import FTEP
from lib_mail import Mailbox
from lib_invoice import Invoice
from lib_idoc.invoice import IDOC
from lib_azure.ai_document_intelligence import FormRecognizer
from lib_utilys import read_json, write_json

ftp = FTEP()
mailbox = Mailbox()
form_recognizer = FormRecognizer()

logging.basicConfig(level=logging.DEBUG)

DATA_ROOT = Path("/data")
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

def main():

    mailbox.initialize_uid(slider_value=1)
    while True:

        try:
            uids = mailbox.list_uids()
            metadata = mailbox.extract_minimal_metadata(uids)
        except Exception:
            logging.exception("Failed to list or extract metadata from emails")
            time.sleep(60)

        for invoice, idoc in mailbox.create_invoice_and_idoc(uids, Invoice, IDOC, STARTSEG_PATH, DYNSEG_PATH, ENDSEG_PATH):
            if mailbox.should_process(CRIT_PATH, invoice):
                logging.info("Processing invoice: %s from %s", invoice.subject, invoice.business)

                # PRODUCTION
                result = form_recognizer.analyze_document(MODELMAP_PATH, invoice)

                # TESTING
                #write_json(RESULT_PATH, result)
                #result = read_json(RESULT_PATH)

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
                    mailbox.flag_email(invoice.uid)
                    continue

                if invoice.type == "NULL":
                    logging.info("Invoice type is NULL, skipping further processing")
                    mailbox.delete_email(invoice.uid)
                    continue

                elif invoice.type == "INVO":
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
                        mailbox.flag_email(invoice.uid)
                    continue
            else:
                logging.info("Email with subject '%s' does not meet criteria for processing", invoice.subject)
                mailbox.uid = invoice.uid
                mailbox.flag_email(invoice.uid)
        
        logging.info("Sleeping for 60 seconds before checking for new emails")
        time.sleep(60)

if __name__ == "__main__":
    main()
                

        
            


            

