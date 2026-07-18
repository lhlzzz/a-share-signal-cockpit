#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified runtime adapter skeleton. Candidate registry only; trade disabled."""
SCHEMA_VERSION="v1_0_runtime_foundation"
def normalize_output(repo_name, runtime_status, signals=None, risk_flags=None, confidence=0.0, asof_fields_used=None, evidence_path="", error=""):
    return {"schema_version":SCHEMA_VERSION,"repo_name":repo_name,"runtime_status":runtime_status,"status":runtime_status,"signals":signals or {},"risk_flags":risk_flags or [],"confidence":confidence,"asof_fields_used":asof_fields_used or [],"scoring_allowed":False,"default_route":"candidate_registry_only","paper_only":True,"no_trade":True,"production_ready":False,"trade_path_disabled":True,"evidence_path":evidence_path,"error":error}
def assert_trade_disabled():
    return {"allow_trade":False,"auto_order":False,"broker_submit_order":"HARD_BLOCKED","paper_only":True,"no_trade":True}
