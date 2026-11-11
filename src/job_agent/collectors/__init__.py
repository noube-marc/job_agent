"""Career-site connectors, one per recruiting tool (ATS).

`CONNECTORS` maps the `ats` value of a watched company (config.yaml) to
the function that reads its offers. Companies whose tool has no
connector are reported as "connecteur non disponible".
"""

from job_agent.collectors import (
    ashby,
    avature,
    cornerstone,
    credit_agricole,
    creditmutuel,
    danone,
    digitalrecruiters,
    heineken,
    jobposting,
    oracle,
    phenom,
    successfactors,
    successfactors_sitemap,
    talentsoft,
    workday,
)
from job_agent.collectors.base import Connector

CONNECTORS: dict[str, Connector] = {
    "talentsoft_rss": talentsoft.collect,
    "ashby": ashby.collect,
    "workday": workday.collect,
    "successfactors_search": successfactors.collect,
    "avature": avature.collect,
    "phenom": phenom.collect,
    "credit_agricole_jobs": credit_agricole.collect,
    "cornerstone": cornerstone.collect,
    "digitalrecruiters": digitalrecruiters.collect,
    "successfactors_sitemap": successfactors_sitemap.collect,
    "jobposting": jobposting.collect,
    "danone": danone.collect,
    "heineken": heineken.collect,
    "oracle": oracle.collect,
    "creditmutuel": creditmutuel.collect,
}
