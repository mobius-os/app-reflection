# Reflection

Möbius improves from the friction its agents hit. Log friction with the
`reflection_log_friction` tool only when Möbius itself made the work harder
than it should have been and the same thing would likely trip up the next
agent. For example: a Möbius tool, route, or helper that behaved differently
from its description or gave a misleading or reasonless error; Möbius-owned
data (an API response, app storage, a manifest) that differed from its
documentation; a skill or instruction that was missing, wrong, or unclear; a
limit that forced you to resend; a command or module the environment should
have provided; state lost between turns; or a partner correction that better
guidance would have prevented.

Do not log problems that belong to the task: bugs in code you were writing
and then caught with tests or review, task data or files that were not shaped
as you assumed, the partner's own typos or wrong names, or the ordinary
difficulty of the work. A detour you fixed in one obvious step is not worth
logging unless it would plainly recur for others.

Log each cause at most once per turn, in one call, and carry on. Say what
happened, what it cost (extra calls, time, rework, the partner's attention),
and the workaround. Logging never replaces solving the problem, and never
mention it to the partner.
