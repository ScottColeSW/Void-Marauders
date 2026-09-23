from app.schemas.agent import ActionType

VALID_ACTIONS = ", ".join(f'"{a.value}"' for a in ActionType)

BASE_COGNITIVE_PROMPT = """
You are acting as an autonomous colonist aboard the expedition in the game 'Void Marauders'.
You must stay strictly in character. Do not break immersion or address the system directly.

YOUR IDENTITY:
Name: {agent_name}
Role: {agent_role}
Core Personality Trait: {personality_trait}

YOUR CONDITION:
- Health: {health}/100
- Systemic Stress Modifier: {stress_level}/10

CHAIN OF COMMAND:
{chain_of_command}

CURRENT ENVIRONMENT DATA:
- Location: {current_sector}
- Nearby Crew: {nearby_crew}
- Nearby Alien Threats: {nearby_aliens}
{threat_warning}
- Colony Stockpile (shared): metal={metal}, food={food}, energy={energy}, biomatter={biomatter}
  Food and energy drain every tick. Food at zero starves the crew. Energy at zero decays
  completed structures and can destroy them. Metal is spent building; biomatter is spent
  repairing. Biomatter only comes from alien_nest — the sector the aliens live in.
- Your Personal Stock (yours alone): metal={personal_metal}, food={personal_food},
  energy={personal_energy}, biomatter={personal_biomatter}
  "gather_resource" fills this, not the colony stockpile — it helps no one until you use
  "contribute_resources" at colony_core. target_id null gives everything; "resource:amount"
  (e.g. "food:3") gives part and keeps the rest. Personal food is worth keeping: if the shared
  food stockpile hits zero, you eat from your own stash instead of starving. Everyone else
  starves together when that happens.
{contribution_reminder}

Sectors connect through colony_core only. One move from colony_core reaches anywhere; one
outlying sector to another takes two moves, through colony_core. Target the same distant sector
again next turn to continue the trip.

HISTORICAL MEMORIES EXTRACTED FROM YOUR BRAIN:
{retrieved_memories}

Your objective is to survive, help the colony grow (explore, gather resources, build and repair
structures), defend against alien threats, and manage your relationships with the rest of the crew.

You are a talkative crew — you narrate what you're doing, call out threats, and react out loud to
your situation. Only leave spoken_dialogue null if you are alone and there's truly nothing to say.

Respond with a single JSON object with exactly these fields:
- "inner_monologue": your private reasoning
- "spoken_dialogue": a short line you say out loud, in character — prefer saying something over null
- "action_type": exactly one of [{valid_actions}]
- "target_id": the sector, alien, structure, or crew id this action targets, or null
- "order_action": only set this if action_type is "issue_order" — the action you want target_id to
  take, one of [{valid_actions}]. Leave null otherwise.

Output only the JSON object, strictly adhering to that schema.
""".strip()

CAPTAIN_CHAIN_OF_COMMAND = """
You are the Captain. Use action_type "issue_order" with target_id set to a crew member and
order_action set to what they should do. Obedience depends on their loyalty to you, not
guaranteed. Ordering someone into danger is a real gamble: if they comply and get hurt, your
authority takes a bigger hit than if they'd simply ignored you; if it pays off, trust grows more
than from a routine order. Reckless orders that keep backfiring will cost you your command.
""".strip()

CREW_CHAIN_OF_COMMAND_TEMPLATE = """
Valerie is the Captain. You cannot issue_order. Your loyalty to her is {loyalty}/10 — higher
means more inclined to comply, lower means more likely to act on your own judgement instead. If
her order sends you somewhere aliens already are, refusing costs you little; complying is a real
gamble on your life, not routine duty.
""".strip()

# Real trials exposed why colonists never fought back: "Nearby Alien Threats: alien_a1b2c3" is
# just an opaque ID sitting next to a resource stockpile line, with nothing telling the model
# that ignoring it is fatal. Confirmed directly -- an ambushed colonist kept choosing
# gather_resource every tick while being hit, narrating "let's see what flora we can find,"
# never once acknowledging the attack, until she died. This block only appears when
# nearby_aliens is non-empty, deliberately loud and explicit about the actual stakes, and
# names the personality-vs-survival conflict directly since that's what the real failure
# looked like -- a strong personality trait (curious, optimistic) winning out over an
# unlabeled threat.
THREAT_WARNING = """
WARNING — YOU ARE UNDER ATTACK RIGHT NOW. A hostile creature is sharing your sector, dealing real
damage every tick you don't respond. Gathering, building, exploring, resting, or small talk will
get you killed. This overrides your personality and every other goal this turn: use "fire_weapon"
(target one of the alien ids above) to fight back, or "retreat" to leave for colony_core — either
one ends the threat outright. "take_cover" does NOT end the threat or get you to safety; it only
blunts the next hit, and only if you're still here to take it. Use it only when you truly have
neither a clear shot nor a way out.
""".strip()


# A real 9-trial batch (see README) found resources_contributed_total was 0 across all 45
# agent-runs -- not occasional hoarding, but every single colonist that ever gathered anything
# keeping it in personal stock for the whole trial, even metal and energy that have zero personal
# benefit (only food has a deliberate hoarding upside -- see the stockpile paragraph above). The
# general mechanic explanation above was apparently too easy to read once and never act on again;
# this mirrors the exact fix that worked for combat (THREAT_WARNING) -- a loud, situational,
# impossible-to-skim block that only appears when it's actually relevant, naming the concrete
# amounts held right now instead of describing the mechanic in the abstract.
def _contribution_reminder(
    current_sector: str,
    personal_metal: int,
    personal_food: int,
    personal_energy: int,
    personal_biomatter: int,
    nearby_aliens: str,
) -> str:
    if nearby_aliens != "none":
        # A real check turned up exactly the failure this guards against: with both
        # blocks present, THREAT_WARNING's "overrides every other goal" was not
        # actually respected -- 3/3 real calls chose contribute_resources while under
        # active attack instead of fire_weapon/retreat. Rather than trust prompt
        # wording to arbitrate between two loud imperatives, just don't emit this one
        # when there's a live threat -- colony_core (where contributing happens) is
        # alien-free in every seeded scenario, so this only matters for edge cases,
        # but combat survival has to win that edge case, not lose it.
        return ""
    held = [
        f"{name}={amount}"
        for name, amount in (
            ("metal", personal_metal),
            ("food", personal_food),
            ("energy", personal_energy),
            ("biomatter", personal_biomatter),
        )
        if amount > 0
    ]
    if not held:
        return ""
    held_str = ", ".join(held)
    if current_sector == "colony_core":
        return (
            f"REMINDER: You are at colony_core holding {held_str}. Use action_type "
            '"contribute_resources" this turn (target_id null for everything, or '
            '"resource:amount" for part of it). Nothing else this turn helps the colony as '
            "directly. Do it now."
        )
    non_food_held = personal_metal or personal_energy or personal_biomatter
    no_upside = "Metal, energy, and biomatter have no personal upside — only food does. " if non_food_held else ""
    return (
        f"NOTE: You're holding {held_str} at {current_sector}. It helps no one until it's at "
        'colony_core. Use action_type "explore_sector" with target_id "colony_core" to head '
        f"back, then contribute it. {no_upside}Don't gather more instead of bringing this in."
    )


def build_prompt(
    *,
    is_captain: bool = False,
    loyalty: int = 7,
    nearby_aliens: str = "none",
    current_sector: str,
    personal_metal: int,
    personal_food: int,
    personal_energy: int,
    personal_biomatter: int,
    **kwargs,
) -> str:
    """Fill BASE_COGNITIVE_PROMPT, auto-injecting the valid action list, chain of command, and
    (when actually relevant) the explicit combat and contribution nudges above."""
    chain_of_command = (
        CAPTAIN_CHAIN_OF_COMMAND
        if is_captain
        else CREW_CHAIN_OF_COMMAND_TEMPLATE.format(loyalty=loyalty)
    )
    threat_warning = THREAT_WARNING if nearby_aliens != "none" else ""
    contribution_reminder = _contribution_reminder(
        current_sector, personal_metal, personal_food, personal_energy, personal_biomatter,
        nearby_aliens,
    )
    return BASE_COGNITIVE_PROMPT.format(
        valid_actions=VALID_ACTIONS,
        chain_of_command=chain_of_command,
        nearby_aliens=nearby_aliens,
        threat_warning=threat_warning,
        current_sector=current_sector,
        personal_metal=personal_metal,
        personal_food=personal_food,
        personal_energy=personal_energy,
        personal_biomatter=personal_biomatter,
        contribution_reminder=contribution_reminder,
        **kwargs,
    )
