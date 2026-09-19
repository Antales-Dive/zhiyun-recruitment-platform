from dataclasses import dataclass


@dataclass(frozen=True)
class MatchScore:
    score: float
    breakdown: dict[str, float]


def _validate_score(value: float) -> None:
    if not 0 <= value <= 100:
        raise ValueError("score must be between 0 and 100")


def calculate_match_score(
    *, skill_score: float, experience_score: float, completeness_score: float
) -> MatchScore:
    values = {
        "skills": skill_score,
        "experience": experience_score,
        "completeness": completeness_score,
    }
    for value in values.values():
        _validate_score(value)

    breakdown = {
        "skills": round(skill_score * 0.4, 2),
        "experience": round(experience_score * 0.4, 2),
        "completeness": round(completeness_score * 0.2, 2),
    }
    return MatchScore(score=round(sum(breakdown.values()), 2), breakdown=breakdown)


def route_candidate(score: float) -> str:
    _validate_score(score)
    if score >= 80:
        return "INTERVIEW"
    if score >= 60:
        return "QUESTIONNAIRE"
    if score >= 40:
        return "TALENT_POOL"
    return "CLOSED"
