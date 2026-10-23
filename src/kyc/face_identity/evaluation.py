"""Private, identity-disjoint 1:N evaluation. No preset acceptance targets or approval.

One probe per independent evaluation identity is required for the pooled binomial
confidence bound. Use separate predeclared runs for repeated-device/lighting conditions;
do not count correlated video frames or repeated scans as independent observations.
"""

from collections import Counter, defaultdict
from datetime import datetime
import math
import time

import numpy as np

from kyc.biometrics.types import FaceEmbedding
from .policy import IdentityPolicy, decide, sha

METRICS = ("fpir", "fnir", "ambiguity_rate", "retry_rate", "pad_attack_acceptance", "pad_genuine_rejection",
           "verification_false_acceptance", "verification_false_rejection")
DIMENSIONS = ("camera", "lighting", "appearance", "user_group")


def measurement(errors, trials, maximum, minimum):
    if not trials:
        return {"errors": errors, "trials": 0, "rate": None, "upper_95": None, "passed": False}
    rate, z = errors / trials, 1.959963984540054
    upper = (rate + z*z/(2*trials) + z*math.sqrt(rate*(1-rate)/trials + z*z/(4*trials*trials))) / (1 + z*z/trials)
    return {"errors": errors, "trials": trials, "rate": rate, "upper_95": upper,
            "passed": trials >= minimum and upper <= maximum}


def percentile(values, quantile):
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


def evaluate(protocol, data):
    policy = IdentityPolicy.model_validate(protocol["policy"])
    limits = protocol["maximum_error_rates"]
    if set(limits) != set(METRICS) or any(not isinstance(value, (int, float)) or not 0 <= value < 1 for value in limits.values()):
        raise ValueError("Predeclare every maximum error rate; no defaults are supplied")
    minimum = protocol["minimum_independent_trials"]
    if type(minimum) is not int or minimum < 1:
        raise ValueError("Predeclare minimum independent trial counts")
    for dimension in DIMENSIONS:
        conditions = protocol["supported_conditions"][dimension]
        if (not isinstance(conditions, list) or not 1 <= len(conditions) <= 64
                or any(not isinstance(value, str) or not 1 <= len(value) <= 80 for value in conditions)
                or len(set(conditions)) != len(conditions)):
            raise ValueError("Declare a bounded nonempty set of device/group conditions")
    if (not isinstance(protocol["maximum_end_to_end_p95_ms"], (int, float))
            or not 0 < protocol["maximum_end_to_end_p95_ms"] < 120000):
        raise ValueError("Predeclare a finite end-to-end latency limit")
    if data["protocol_sha256"] != sha(protocol):
        raise ValueError("Evaluation does not match the frozen protocol")
    locked, started = datetime.fromisoformat(protocol["locked_at"]), datetime.fromisoformat(data["evaluation_started_at"])
    if locked.tzinfo is None or started.tzinfo is None or locked >= started:
        raise ValueError("Lock acceptance criteria before held-out evaluation")
    development, held_out = set(data["development_subjects"]), set(data["evaluation_subjects"])
    if not development or not held_out or development.intersection(held_out):
        raise ValueError("Development and held-out identities must be disjoint")
    gallery, probes = data["gallery"], data["probes"]
    if not gallery or not probes or len({row["subject"] for row in gallery}) != len(gallery):
        raise ValueError("Use one template per distinct gallery identity")
    if len({row["subject"] for row in probes}) != len(probes):
        raise ValueError("Correlated probes are not independent identity trials")
    used = {row["subject"] for row in gallery + probes}
    if not used <= held_out:
        raise ValueError("Every gallery/probe identity must be declared held out")
    def vector(row):
        if row["model_id"] != policy.model_id:
            raise ValueError("Incompatible model versions")
        return FaceEmbedding(np.array(row["embedding"], dtype=np.float32)).vector
    matrix = np.stack([vector(row) for row in gallery])
    identities = [row["subject"] for row in gallery]
    population_indexes = {population: [index for index, row in enumerate(gallery) if row["population"] == population]
                          for population in policy.populations}
    if any(row["population"] not in policy.populations for row in gallery + probes):
        raise ValueError("Every gallery/probe must name an approved target population")
    counts, trials = Counter(), Counter()
    groups = {dimension: defaultdict(list) for dimension in DIMENSIONS}
    search_ms, end_to_end_ms = [], []
    population_observations = defaultdict(list)
    for row in probes:
        population = row["population"]
        indexes = population_indexes[population]
        scoped_identities = [identities[index] for index in indexes]
        is_known = row["subject"] in scoped_identities
        if row.get("capture_outcome", "VALID") == "VALID":
            probe = vector(row)
            start = time.perf_counter()
            scores = matrix[indexes] @ probe
            outcome, matched = decide(list(zip(scoped_identities, map(float, scores))), policy)
            search_ms.append((time.perf_counter() - start) * 1000)
        else:
            outcome, matched = row["capture_outcome"], None
            if outcome not in {"RETRY_REQUIRED", "LIVENESS_FAILED", "EXPIRED", "TECHNICAL_ERROR"}:
                raise ValueError("Invalid capture outcome")
        failed_identity = outcome != "MATCHED" or matched != row["subject"]
        population_observations[population].append((is_known, failed_identity, outcome))
        name = "fnir" if is_known else "fpir"
        trials[name] += 1
        counts[name] += failed_identity if is_known else outcome == "MATCHED"
        for metric, error in (("ambiguity_rate", outcome == "AMBIGUOUS"),
                              ("retry_rate", outcome in {"RETRY_REQUIRED", "LIVENESS_FAILED", "EXPIRED", "TECHNICAL_ERROR"})):
            trials[metric] += 1
            counts[metric] += error
        duration = row.get("end_to_end_ms")
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Supply measured capture-to-authorized-result latency for each scan")
        end_to_end_ms.append(duration)
        for dimension in DIMENSIONS:
            group = row[dimension]
            if group not in protocol["supported_conditions"][dimension]:
                raise ValueError("Undeclared evaluation condition")
            groups[dimension][group].append((is_known, failed_identity, outcome))
    pad_seen, verification_seen = set(), set()
    for row in data.get("pad_trials", []):
        if row["subject"] not in held_out or type(row["accepted"]) is not bool or type(row["attack"]) is not bool:
            raise ValueError("PAD trials need held-out labels and provider decisions")
        name = "pad_attack_acceptance" if row["attack"] else "pad_genuine_rejection"
        if (name, row["subject"]) in pad_seen:
            raise ValueError("Correlated PAD observations need a separate evaluation protocol")
        pad_seen.add((name, row["subject"]))
        trials[name] += 1
        counts[name] += row["accepted"] if row["attack"] else not row["accepted"]
    for row in data.get("verification_trials", []):
        if row["reference"]["subject"] not in held_out or row["probe"]["subject"] not in held_out:
            raise ValueError("Enrollment verification identities must be held out")
        same = row["reference"]["subject"] == row["probe"]["subject"]
        accepted = float(np.dot(vector(row["reference"]), vector(row["probe"]))) >= policy.verification_threshold
        name = "verification_false_rejection" if same else "verification_false_acceptance"
        if (name, row["probe"]["subject"]) in verification_seen:
            raise ValueError("Repeated verification probes are not independent observations")
        verification_seen.add((name, row["probe"]["subject"]))
        trials[name] += 1
        counts[name] += not accepted if same else accepted
    metrics = {name: measurement(counts[name], trials[name], limits[name], minimum) for name in METRICS}
    group_report, groups_passed = {}, True
    for dimension in DIMENSIONS:
        group_report[dimension] = {}
        for group in protocol["supported_conditions"][dimension]:
            observations = groups[dimension][group]
            group_metrics = {}
            for metric, select, failure in (
                ("fnir", lambda r: r[0], lambda r: r[1]),
                ("fpir", lambda r: not r[0], lambda r: r[2] == "MATCHED")):
                selected = [row for row in observations if select(row)]
                group_metrics[metric] = measurement(sum(failure(row) for row in selected), len(selected), limits[metric], minimum)
            group_report[dimension][group] = group_metrics
            groups_passed &= all(row["passed"] for row in group_metrics.values())
    latency_passed = percentile(end_to_end_ms, .95) <= protocol["maximum_end_to_end_p95_ms"]
    synthetic = data.get("synthetic") is not False
    population_report = {}
    for population in policy.populations:
        observations = population_observations[population]
        group_metrics = {}
        for metric, select, failure in (
            ("fnir", lambda r: r[0], lambda r: r[1]),
            ("fpir", lambda r: not r[0], lambda r: r[2] == "MATCHED")):
            selected = [row for row in observations if select(row)]
            group_metrics[metric] = measurement(sum(failure(row) for row in selected), len(selected), limits[metric], minimum)
        population_report[population] = {"gallery_size": len(population_indexes[population]), "metrics": group_metrics}
    tested_size = min(len(indexes) for indexes in population_indexes.values())
    population_passed = tested_size >= policy.max_population and all(
        metric["passed"] for row in population_report.values() for metric in row["metrics"].values())
    return {"schema": "face-identity-evaluation-v1", "synthetic": synthetic,
        "ready": not synthetic and population_passed and groups_passed and latency_passed and all(row["passed"] for row in metrics.values()),
        "identity_disjoint": True, "criteria_predeclared": True, "policy_sha256": sha(protocol["policy"]),
        "protocol_sha256": sha(protocol), "dataset_sha256": sha(data),
        "search_method": "EXACT", "candidate_recall": 1.0, "max_population_tested": tested_size,
        "populations_tested": list(policy.populations), "population_results": population_report,
        "metrics": metrics, "conditions": group_report,
        "latency": {"exact_search_p50_ms": percentile(search_ms, .5), "exact_search_p95_ms": percentile(search_ms, .95),
                    "end_to_end_p95_ms": percentile(end_to_end_ms, .95), "end_to_end_passed": latency_passed},
        "limitations": ["No image-level accuracy is established by precomputed embeddings alone.",
                        "PAD and end-to-end measurements are supplied by the device evaluation harness.",
                        "Wilson intervals assume independent evaluation identities; correlated scans need a separate protocol.",
                        "Governance approval, occlusion, device and restore evidence are separate requirements."]}
