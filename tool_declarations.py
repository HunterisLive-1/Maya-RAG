"""Single Gemini Live tool — query engineering PDF knowledge base."""

from google.genai import types

QUERY_BOOKS_TOOL = types.FunctionDeclaration(
    name="query_engineering_books",
    description="""Search the loaded engineering reference books for technical information.

    ALWAYS call this tool when user asks about:
    - Boilers: types (fire-tube, water-tube, AFBC, CFBC), components, drum, furnace, burners, safety valves
    - Steam systems: pressure, temperature, superheat, reheat, steam tables, properties
    - Power plant operations: startup, shutdown, load following, normal operations
    - Thermodynamics: Rankine cycle, efficiency, heat balance, enthalpy, entropy
    - Equipment: turbines, condensers, feedwater heaters, economizers, air preheaters, BFP
    - Instrumentation and controls: pressure gauges, flow meters, level controls, interlocks
    - Troubleshooting: alarms, trips, faults, abnormal conditions, emergency procedures
    - Safety: safety interlocks, protective systems, hazards, LOTO procedures
    - Combustion: fuel systems, air-fuel ratio, excess air, combustion efficiency
    - Water chemistry: feedwater treatment, boiler water, pH, TDS, blowdown
    - Draft systems: FD fan, ID fan, balanced draft, natural draft
    - Heat transfer: radiation, convection, conduction in boiler components

    Do NOT answer engineering questions from memory — always query the books first.
    The books contain authoritative, specific technical data for this domain.""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Specific engineering question or topic to search. Be specific and technical. "
                    "Example: 'boiler drum water level control three-element control system' or "
                    "'AFBC boiler bed temperature control'"
                ),
            ),
            "book_filter": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Optional: specific book_id to search only that book. "
                    "Leave empty to search all loaded books."
                ),
            ),
        },
        required=["query"],
    ),
)

ALL_TOOLS = [QUERY_BOOKS_TOOL]

TOOL_FUNCTION_MAP = {
    "query_engineering_books": None,
}
