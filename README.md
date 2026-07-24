# FAC - Job Tracker

FAC started as a project to keep me busy and hopefully do something useful
with my time after I was laid off.

Publicly, FAC stands for **Fully Automated Candidate**. The idea is simple:
if corporations use applicant tracking systems to collect, filter, and
track candidates, job seekers should be able to use automation too.

FAC collects public job postings, removes obvious noise, scores
opportunities using transparent deterministic rules, and uses AI only
when it is actually useful. The goal is to spend less time digging through
job-board landfill and more time reviewing jobs that may genuinely be
worth applying to.

FAC currently supports **Greenhouse, Ashby, and Lever**. Workday, iCIMS,
and other platforms are future goals. Because those systems are harder to
access consistently through public endpoints, targeted support for
specific companies may be more practical than trying to crawl every
employer using those platforms.

LinkedIn scraping is not currently a priority. Reposted jobs, expired
listings, and one opportunity duplicated across many locations create
exactly the kind of noise FAC is intended to reduce.

## Editions

### [Core edition](core/)

Standalone Python scripts for people who prefer direct control.

### [Desktop edition](desktop/)

A guided local browser interface with Mac and Windows installation tools.

Both editions are included in this repository and use project version
**4.7**.

## Local-first design

FAC runs on the user's own computer. Résumés, preferences, caches, saved
jobs, and generated reports remain local. API keys are read by Python from
a local `.env` file and are never exposed to browser JavaScript.

FAC includes no telemetry, analytics, advertising, or default data
collection.

The collector necessarily sends requests to the public ATS endpoints
selected by the user. When optional AI is enabled, the information needed
for that analysis is sent to the configured OpenAI API account.

## Contributing

Frontend work is not my strongest area, so contributions are especially
welcome there. Accessibility, usability, documentation, tests, new ATS
integrations, and careful improvements to the collection process are also
welcome.

## License

FAC is free for individual job seekers under the
[FAC Job Seeker Community License](LICENSE.txt).

Individuals may use it, modify it, fork it, self-host it, and share it with
other job seekers. The restrictions are intended to prevent corporate,
recruiting, HR, and commercial exploitation.

Good luck with your job search. I hope FAC makes it a little less
miserable.
