FAC - JOB TRACKER
=================

FAC - Job Tracker is a private local application for collecting,
scoring, organizing, and analyzing job opportunities.


WINDOWS INSTALLATION
--------------------

1. Unzip the package.
2. Double-click:

       Install FAC - Job Tracker.bat

3. A native Windows folder picker asks where to install it.
4. The installer creates:

       FAC - Job Tracker

   inside the folder you selected.
5. It creates a private Python environment, installs dependencies,
   and starts the application.

To start it later, open the installed folder and double-click:

       Start FAC - Job Tracker.bat

If Windows SmartScreen blocks the installer, choose:

       More info
       Run anyway

You can also right-click the downloaded ZIP or BAT file, choose
Properties, check Unblock when shown, and click Apply.

Python 3 must be installed. During Python installation, enable:

       Add Python to PATH


MAC INSTALLATION
----------------

1. Unzip the package.
2. Double-click:

       Install FAC - Job Tracker.command

3. A native macOS folder picker asks where to install it.
4. The installer creates:

       FAC - Job Tracker

   inside the folder you selected.
5. It creates a private Python environment, installs dependencies,
   and starts the application.

To start it later, open the installed folder and double-click:

       Start FAC - Job Tracker.command

If macOS blocks the installer, try opening it once, then go to:

       System Settings
       Privacy & Security
       Open Anyway


APPLICATION ADDRESS
-------------------

The application opens in the default browser at:

       http://127.0.0.1:8765

It binds only to the user's own computer.


UPDATING AN EXISTING INSTALLATION
---------------------------------

Run the installer for the operating system again and select the same
parent folder.

The installer updates application files while preserving:

- .env
- output/
- ATS board caches
- scoring cache
- job history
- saved application settings
- AI caches


OPENAI KEY AND .ENV
-------------------

AI is optional and disabled by default.

A new installation creates one configuration file:

       .env

It initially contains:

       OPENAI_API_KEY=
       OPENAI_MODEL=gpt-5-mini

Add a key only when optional AI market interpretation and opportunity
coaching are wanted.

The browser never receives or displays the key. The local Python backend
reads it directly from .env.

Running the installer again preserves the existing .env and never replaces
the user's key or model setting.

Because filenames beginning with a period are hidden by default:

- On Mac, press Command + Shift + . in Finder to show hidden files.
- On Windows, enable View > Show > Hidden items in File Explorer.

Without an API key, FAC - Job Tracker still collects and scores jobs and
generates verified local market statistics without API cost.


IMPORTANT SPEED NOTE
--------------------

Leave "Force complete ATS board rediscovery" unchecked for normal runs.

A complete rediscovery intentionally tests every candidate board and is
substantially slower.

LICENSE
-------

FAC is free for individual job seekers.

You may use it, change it, fork it, self-host it, and share it with other
job seekers. Shared versions should keep the source available and use the
same License.

The restrictions are aimed at companies and organizations. They may not
commercialize, rebrand, incorporate, or use FAC for recruiting, candidate
screening, employment decisions, or competing products without written
permission.

Read:

    LICENSE.txt
    THIRD_PARTY_NOTICES.txt
    CONTRIBUTING.md

This custom License has not been reviewed by an attorney. Legal review is
recommended before relying on it in a dispute or a large public release.

DEPENDENCIES
------------

The installer creates a private .venv inside the selected FAC folder and
installs the packages listed in requirements_app.txt:

       FastAPI
       Uvicorn
       Requests
       Pydantic

These dependencies are installed automatically on both Mac and Windows.
They do not need to be installed manually into the user's system Python.

