"""Career-site connectors, one per recruiting tool (ATS).

`CONNECTORS` maps the `ats` value of a watched company (config.yaml) to
the function that reads its offers. Companies whose tool has no
connector are reported as "connecteur non disponible".
"""

from job_agent.collectors import ashby, avature, credit_agricole, phenom, successfactors, talentsoft, workday
from job_agent.collectors.base import Connector

CONNECTORS: dict[str, Connector] = {
    "talentsoft_rss": talentsoft.collect,
    "ashby": ashby.collect,
    "workday": workday.collect,
    "successfactors_search": successfactors.collect,
    "avature": avature.collect,
    "phenom": phenom.collect,
    "credit_agricole_jobs": credit_agricole.collect,
}
