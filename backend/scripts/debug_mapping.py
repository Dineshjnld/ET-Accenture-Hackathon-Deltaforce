import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models.controls import Control
from app.models.obligations import Obligation
from app.services.mapping import suggest_mappings, _keywords, _expand
from sqlalchemy.orm import Session

# Fake session: suggest_mappings only uses session when controls=None or embeddings.
# We pass controls explicitly and use_vectors=False, so session is unused.
class FakeSession:
    pass

def C(uid, title, obj, act, owner, freq, exp, test):
    return Control(control_uid=uid, title=title, control_objective=obj,
                   control_activity=act, owner_role=owner, frequency=freq,
                   expected_evidence=exp, testing_procedure=test)

controls = [
    C("CTL-IO-APPOINT", "Appoint and annually review Internal Ombudsman strength",
      "Ensure the IO office is staffed per the RBI scheme",
      "Appoint at least one IO and have the CSC review IO strength at least once a year",
      "Head of Customer Service", "annual",
      "CSC minutes; IO appointment letter", "Inspect latest CSC minutes and appointment file"),
    C("CTL-IO-VACANCY", "Report IO vacancies to RBI within deadline",
      "Ensure RBI is informed of IO vacancies on time",
      "On any IO vacancy inform RBI within 10 working days with dispatch proof",
      "Compliance Officer", "event-driven",
      "RBI intimation letter with dispatch proof", "Trace vacancy events to dispatch records"),
    C("CTL-IO-ESCALATE", "Escalate rejected complaints to the IO",
      "Ensure no rejected complaint bypasses the IO",
      "Escalate every rejected or partly redressed complaint to the IO before final communication",
      "Grievance Redressal Officer", "ongoing",
      "Escalation register with IO remarks", "Re-perform a sample of 25 escalated complaints"),
]

obls = [
    "Every bank shall appoint at least one IO.",
    "In case of vacancy the bank shall inform Reserve Bank of India within 10 working days from the date of such vacancy and shall appoint a new IO.",
    "The Customer Service Committee of the Board of the bank shall determine, at least once in every year, the number of IOs required.",
    "All complaints rejected by the internal grievance machinery shall be escalated to the Internal Ombudsman.",
]

for text in obls:
    o = Obligation(obligation_uid="T", regulation_id="r", regulation_version_id="v",
                   quoted_text=text, obligation_text=text)
    cands = suggest_mappings(FakeSession(), o, controls=controls, use_vectors=False, min_confidence=0.0)
    print("=" * 110)
    print("OBL:", text[:100])
    for c in cands:
        print(f"  {c.control_id} conf={c.confidence} lex={c.lexical_score} vec={c.vector_score} shared={c.shared_terms}")
