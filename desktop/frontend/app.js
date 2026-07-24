const state = {
    config: null,
    defaultConfig: null,
    companies: [],
    categories: [],
    selectedCompanies: new Set(),
    apiKey: {
        configured: false,
        model: "gpt-5-mini",
    },
    activeSection: "profile",
    pollTimer: null,
    lastRunStatus: null,
    dirty: false,
};

const pageTitles = {
    profile: "Build your search profile",
    companies: "Choose the company universe",
    rules: "Tune the scoring engine",
    run: "Review and run your search",
    progress: "Search progress",
    results: "Open your results",
};

function byId(id) {
    return document.getElementById(id);
}

function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
}

function linesToArray(value) {
    const seen = new Set();

    return String(value || "")
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter((item) => {
            if (!item) {
                return false;
            }

            const key = item.toLowerCase();

            if (seen.has(key)) {
                return false;
            }

            seen.add(key);
            return true;
        });
}

function arrayToLines(value) {
    return Array.isArray(value) ? value.join("\n") : "";
}

function formatCategory(value) {
    return String(value || "")
        .split("_")
        .map((word) => (
            word.charAt(0).toUpperCase() + word.slice(1)
        ))
        .join(" ");
}

function setDirty(dirty = true) {
    state.dirty = dirty;
    const button = byId("saveButton");

    if (!button) {
        return;
    }

    button.textContent = dirty
        ? "Save settings •"
        : "Saved";
}

function showMessage(message, type = "") {
    const element = byId("globalMessage");

    element.textContent = message;
    element.className = "global-message";

    if (type) {
        element.classList.add(type);
    }

    element.classList.remove("hidden");

    window.clearTimeout(showMessage.timeout);
    showMessage.timeout = window.setTimeout(() => {
        element.classList.add("hidden");
    }, 4200);
}

async function api(path, options = {}) {
    const response = await fetch(path, {
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
        ...options,
    });

    let payload = {};

    try {
        payload = await response.json();
    } catch {
        payload = {};
    }

    if (!response.ok) {
        const message = (
            payload.detail
            || payload.message
            || `Request failed with status ${response.status}`
        );
        throw new Error(message);
    }

    return payload;
}

function showSection(sectionName) {
    const section = document.getElementById(sectionName);

    if (!section) {
        return;
    }

    state.activeSection = sectionName;

    document.querySelectorAll(".app-section").forEach((item) => {
        item.classList.toggle(
            "active",
            item.id === sectionName,
        );
    });

    document.querySelectorAll(".nav-item").forEach((item) => {
        item.classList.toggle(
            "active",
            item.dataset.section === sectionName,
        );
    });

    byId("pageTitle").textContent = (
        pageTitles[sectionName]
        || "FAC - Job Tracker"
    );

    history.replaceState(
        null,
        "",
        `#${sectionName}`,
    );

    window.scrollTo({
        top: 0,
        behavior: "instant",
    });
}

function weightedHeader() {
    const header = document.createElement("div");
    header.className = "weighted-table-header";
    header.innerHTML = `
        <span>Term or phrase</span>
        <span>Points</span>
        <span></span>
    `;
    return header;
}

function createWeightedRow(term = "", points = "") {
    const template = byId("weightedRowTemplate");
    const row = template.content.firstElementChild.cloneNode(true);

    row.querySelector(".term-input").value = term;
    row.querySelector(".points-input").value = points;

    row.querySelector(".remove-weighted").addEventListener(
        "click",
        () => {
            row.remove();
            updateWeightedCounts();
            setDirty();
        },
    );

    row.querySelectorAll("input").forEach((input) => {
        input.addEventListener("input", () => {
            updateWeightedCounts();
            setDirty();
        });
    });

    return row;
}

function renderWeightedEditor(ruleName, terms) {
    const container = document.querySelector(
        `.weighted-editor[data-rule="${ruleName}"]`,
    );

    if (!container) {
        return;
    }

    container.innerHTML = "";
    container.appendChild(weightedHeader());

    Object.entries(terms || {}).forEach(([term, points]) => {
        container.appendChild(
            createWeightedRow(term, points),
        );
    });

    const addButton = document.createElement("button");
    addButton.type = "button";
    addButton.className = "button compact secondary add-weighted";
    addButton.textContent = "Add term";

    addButton.addEventListener("click", () => {
        const row = createWeightedRow();
        container.insertBefore(row, addButton);
        row.querySelector(".term-input").focus();
        updateWeightedCounts();
        setDirty();
    });

    container.appendChild(addButton);
}

function collectWeightedEditor(ruleName) {
    const container = document.querySelector(
        `.weighted-editor[data-rule="${ruleName}"]`,
    );
    const result = {};

    if (!container) {
        return result;
    }

    container.querySelectorAll(".weighted-row").forEach((row) => {
        const term = row.querySelector(".term-input").value.trim();
        const rawPoints = row.querySelector(".points-input").value;
        const points = Number.parseInt(rawPoints, 10);

        if (!term || Number.isNaN(points)) {
            return;
        }

        result[term] = points;
    });

    return result;
}

function updateWeightedCounts() {
    document.querySelectorAll("[data-count-for]").forEach((element) => {
        const ruleName = element.dataset.countFor;
        const container = document.querySelector(
            `.weighted-editor[data-rule="${ruleName}"]`,
        );
        const count = container
            ? container.querySelectorAll(".weighted-row").length
            : 0;

        element.textContent = `${count} terms`;
    });
}

function populateProfile() {
    const profile = state.config.profile || {};

    byId("resumeText").value = profile.resume_text || "";
    byId("desiredJobText").value = (
        profile.desired_job_text || ""
    );
    byId("locationPreference").value = (
        profile.location_preference || ""
    );
    byId("additionalNotes").value = (
        profile.additional_notes || ""
    );
}

function populateRules() {
    const rules = state.config.rules || {};

    renderWeightedEditor(
        "target_role_phrases",
        rules.target_role_phrases,
    );
    renderWeightedEditor(
        "title_specialty_terms",
        rules.title_specialty_terms,
    );
    renderWeightedEditor(
        "domain_terms",
        rules.domain_terms,
    );
    renderWeightedEditor(
        "customer_facing_terms",
        rules.customer_facing_terms,
    );

    const combinedExcludedTerms = Array.from(
        new Set([
            ...(rules.excluded_title_terms || []),
            ...(rules.hard_entry_title_terms || []),
        ]),
    );

    byId("excludedJobTerms").value = arrayToLines(
        combinedExcludedTerms,
    );
    byId("excludedExperienceLevels").value = arrayToLines(
        rules.excluded_experience_levels,
    );
    byId("localAreaTerms").value = arrayToLines(
        rules.local_area_terms,
    );
    byId("remoteTerms").value = arrayToLines(
        rules.remote_terms,
    );
    byId("hybridTerms").value = arrayToLines(
        rules.hybrid_terms,
    );
    byId("onsiteTerms").value = arrayToLines(
        rules.onsite_terms,
    );

    byId("minPossibleScore").value = (
        rules.min_possible_score ?? 50
    );
    byId("minStrongScore").value = (
        rules.min_strong_score ?? 65
    );

    updateWeightedCounts();
}

function populateSourcesAndAgent() {
    const sources = state.config.sources || {};
    const agent = state.config.agent || {};

    byId("sourceGreenhouse").checked = (
        sources.greenhouse !== false
    );
    byId("sourceAshby").checked = (
        sources.ashby !== false
    );
    byId("sourceLever").checked = (
        sources.lever !== false
    );

    byId("aiModel").value = (
        agent.model
        || state.apiKey.model
        || "gpt-5-mini"
    );
    byId("topJobs").value = agent.top_jobs || 15;

    byId("runAi").checked = false;
    byId("refreshAi").checked = false;
    byId("forceDiscovery").checked = false;

    updateAiAvailability();
    updateRunSummary();
}

function populateCategories() {
    const select = byId("companyCategory");
    select.innerHTML = '<option value="">All categories</option>';

    state.categories.forEach((category) => {
        const option = document.createElement("option");
        option.value = category;
        option.textContent = formatCategory(category);
        select.appendChild(option);
    });
}

function visibleCompanies() {
    const query = byId("companySearch").value
        .trim()
        .toLowerCase();
    const category = byId("companyCategory").value;

    return state.companies.filter((company) => {
        const matchesQuery = (
            !query
            || company.name.toLowerCase().includes(query)
        );
        const matchesCategory = (
            !category
            || company.categories.includes(category)
        );

        return matchesQuery && matchesCategory;
    });
}

function renderCompanies() {
    const container = byId("companyList");
    const companies = visibleCompanies();

    container.innerHTML = "";

    if (!companies.length) {
        container.innerHTML = `
            <div class="empty-list">
                No companies match this filter.
            </div>
        `;
        return;
    }

    const fragment = document.createDocumentFragment();

    companies.forEach((company) => {
        const label = document.createElement("label");
        label.className = "company-option";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = state.selectedCompanies.has(
            company.name,
        );

        checkbox.addEventListener("change", () => {
            if (checkbox.checked) {
                state.selectedCompanies.add(company.name);
            } else {
                state.selectedCompanies.delete(company.name);
            }

            updateCompanyCount();
            updateRunSummary();
            setDirty();
        });

        const text = document.createElement("span");
        const categories = company.categories
            .map(formatCategory)
            .join(" · ");

        text.innerHTML = `
            <strong>${escapeHtml(company.name)}</strong>
            <small>
                ${escapeHtml(categories)}
                · Priority ${Number(company.priority)}
            </small>
        `;

        label.append(checkbox, text);
        fragment.appendChild(label);
    });

    container.appendChild(fragment);
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = String(value ?? "");
    return div.innerHTML;
}

function updateCompanyCount() {
    const customCount = (
        state.config.companies?.custom?.length || 0
    );
    const count = state.selectedCompanies.size + customCount;

    byId("enabledCompanyCount").textContent = (
        count.toLocaleString()
    );
}

function renderCustomCompanies() {
    const container = byId("customCompanyList");
    const companies = state.config.companies.custom || [];

    container.innerHTML = "";

    if (!companies.length) {
        container.innerHTML = `
            <div class="empty-list">
                No custom companies added.
            </div>
        `;
        return;
    }

    companies.forEach((company, index) => {
        const row = document.createElement("div");
        row.className = "custom-company-row";

        row.innerHTML = `
            <strong>${escapeHtml(company.name)}</strong>
            <span>${escapeHtml(
                company.ats === "auto"
                    ? "Auto-detect"
                    : formatCategory(company.ats)
            )}</span>
            <span>${escapeHtml(
                company.token || "Normalized company name"
            )}</span>
            <button
                class="icon-button"
                type="button"
                aria-label="Remove custom company"
            >×</button>
        `;

        row.querySelector("button").addEventListener(
            "click",
            () => {
                state.config.companies.custom.splice(
                    index,
                    1,
                );
                renderCustomCompanies();
                updateCompanyCount();
                updateRunSummary();
                setDirty();
            },
        );

        container.appendChild(row);
    });
}

function addCustomCompany() {
    const name = byId("customCompanyName").value.trim();
    const ats = byId("customCompanyAts").value;
    const token = byId("customCompanyToken").value.trim();

    if (!name) {
        showMessage(
            "Enter a company name before adding it.",
            "error",
        );
        return;
    }

    const exists = state.config.companies.custom.some(
        (company) => (
            company.name.toLowerCase() === name.toLowerCase()
            && company.ats === ats
        ),
    );

    if (exists) {
        showMessage(
            "That custom company and ATS combination already exists.",
            "error",
        );
        return;
    }

    state.config.companies.custom.push({
        name,
        ats,
        token,
    });

    byId("customCompanyName").value = "";
    byId("customCompanyToken").value = "";
    byId("customCompanyAts").value = "auto";

    renderCustomCompanies();
    updateCompanyCount();
    updateRunSummary();
    setDirty();
}

function gatherConfigFromForm() {
    const config = state.config;

    config.profile = {
        resume_text: byId("resumeText").value.trim(),
        desired_job_text: byId("desiredJobText").value.trim(),
        location_preference: (
            byId("locationPreference").value.trim()
        ),
        additional_notes: (
            byId("additionalNotes").value.trim()
        ),
    };

    config.companies.enabled = Array.from(
        state.selectedCompanies,
    ).sort((left, right) => left.localeCompare(right));

    config.sources = {
        greenhouse: byId("sourceGreenhouse").checked,
        ashby: byId("sourceAshby").checked,
        lever: byId("sourceLever").checked,
    };

    config.rules = {
        target_role_phrases: collectWeightedEditor(
            "target_role_phrases",
        ),
        title_specialty_terms: collectWeightedEditor(
            "title_specialty_terms",
        ),
        domain_terms: collectWeightedEditor(
            "domain_terms",
        ),
        customer_facing_terms: collectWeightedEditor(
            "customer_facing_terms",
        ),
        excluded_title_terms: linesToArray(
            byId("excludedJobTerms").value,
        ),
        excluded_experience_levels: linesToArray(
            byId("excludedExperienceLevels").value,
        ),
        hard_entry_title_terms: linesToArray(
            byId("excludedJobTerms").value,
        ),
        local_area_terms: linesToArray(
            byId("localAreaTerms").value,
        ),
        remote_terms: linesToArray(
            byId("remoteTerms").value,
        ),
        hybrid_terms: linesToArray(
            byId("hybridTerms").value,
        ),
        onsite_terms: linesToArray(
            byId("onsiteTerms").value,
        ),
        min_possible_score: Number.parseInt(
            byId("minPossibleScore").value,
            10,
        ) || 50,
        min_strong_score: Number.parseInt(
            byId("minStrongScore").value,
            10,
        ) || 65,
    };

    config.agent = {
        ...(config.agent || {}),
        model: byId("aiModel").value.trim() || "gpt-5-mini",
        top_jobs: Math.max(
            1,
            Math.min(
                20,
                Number.parseInt(
                    byId("topJobs").value,
                    10,
                ) || 15,
            ),
        ),
    };

    return config;
}

async function saveConfig(showConfirmation = true) {
    const config = gatherConfigFromForm();

    await api("/api/config", {
        method: "POST",
        body: JSON.stringify({ config }),
    });

    state.config = config;
    setDirty(false);

    if (showConfirmation) {
        showMessage(
            "Settings saved locally.",
            "success",
        );
    }
}

function updateApiKeyStatus() {
    const chip = byId("apiKeyStatus");

    if (state.apiKey.configured) {
        chip.className = "status-chip good";
        chip.textContent = `OpenAI key configured · ${state.apiKey.model}`;
    } else {
        chip.className = "status-chip warning";
        chip.textContent = "OpenAI key not configured";
    }
}

function updateAiAvailability() {
    const runAi = byId("runAi");
    const masterToggle = runAi.closest(".master-toggle");
    const options = byId("aiOptions");
    const help = byId("aiToggleHelp");

    if (!state.apiKey.configured) {
        runAi.checked = false;
        runAi.disabled = true;
        masterToggle.classList.add("disabled");
        help.textContent = (
            "Add OPENAI_API_KEY to .env to enable AI."
        );
    } else {
        runAi.disabled = false;
        masterToggle.classList.remove("disabled");
        help.textContent = (
            "Uses your locally configured API key."
        );
    }

    options.classList.toggle(
        "disabled",
        !runAi.checked,
    );

    updateRunSummary();
}

function enabledSourceNames() {
    const sources = [];

    if (byId("sourceGreenhouse").checked) {
        sources.push("Greenhouse");
    }

    if (byId("sourceAshby").checked) {
        sources.push("Ashby");
    }

    if (byId("sourceLever").checked) {
        sources.push("Lever");
    }

    return sources;
}

function updateRunSummary() {
    if (!state.config) {
        return;
    }

    const customCount = (
        state.config.companies?.custom?.length || 0
    );
    const companyCount = (
        state.selectedCompanies.size + customCount
    );
    const sources = enabledSourceNames();
    const possible = byId("minPossibleScore").value || "50";
    const strong = byId("minStrongScore").value || "65";
    const runAi = byId("runAi").checked;

    byId("summaryCompanies").textContent = (
        `${companyCount.toLocaleString()} enabled`
    );
    byId("summarySources").textContent = (
        sources.length ? sources.join(", ") : "None"
    );
    byId("summaryThresholds").textContent = (
        `${possible}+ possible · ${strong}+ strong`
    );
    byId("summaryAi").textContent = runAi
        ? `${byId("aiModel").value || "gpt-5-mini"}, top ${byId("topJobs").value || 15}`
        : "Local statistics only — no API cost";
}

function validateRun() {
    const sources = enabledSourceNames();

    if (!sources.length) {
        return "Enable at least one ATS source.";
    }

    const companyCount = (
        state.selectedCompanies.size
        + (state.config.companies.custom || []).length
    );

    if (!companyCount) {
        return "Enable or add at least one company.";
    }

    const possible = Number.parseInt(
        byId("minPossibleScore").value,
        10,
    );
    const strong = Number.parseInt(
        byId("minStrongScore").value,
        10,
    );

    if (
        Number.isNaN(possible)
        || Number.isNaN(strong)
        || strong < possible
    ) {
        return (
            "The strong-match threshold must be equal to or "
            + "higher than the possible-match threshold."
        );
    }

    if (
        byId("runAi").checked
        && !byId("resumeText").value.trim()
    ) {
        return (
            "Paste résumé text before running personalized AI coaching."
        );
    }

    return "";
}

async function startRun() {
    gatherConfigFromForm();
    const validationError = validateRun();

    if (validationError) {
        showMessage(validationError, "error");
        return;
    }

    await saveConfig(false);

    const request = {
        config: state.config,
        run_ai: byId("runAi").checked,
        refresh_ai: byId("refreshAi").checked,
        force_discovery: byId("forceDiscovery").checked,
        top_jobs: Number.parseInt(
            byId("topJobs").value,
            10,
        ) || 15,
    };

    try {
        byId("runButton").disabled = true;
        byId("runButton").textContent = "Starting…";

        await api("/api/run", {
            method: "POST",
            body: JSON.stringify(request),
        });

        showSection("progress");
        startPolling();
    } catch (error) {
        showMessage(error.message, "error");
    } finally {
        byId("runButton").disabled = false;
        byId("runButton").textContent = "Run job search";
    }
}

function formatDateTime(value) {
    if (!value) {
        return "Not started";
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
        return value;
    }

    return date.toLocaleString();
}

function updateResultLinks(status) {
    const jobsButton = byId("openJobsButton");
    const intelligenceButton = byId(
        "openIntelligenceButton",
    );

    if (status.job_dashboard_ready) {
        jobsButton.href = status.job_dashboard_url;
        jobsButton.classList.remove("disabled");
    } else {
        jobsButton.href = "#";
        jobsButton.classList.add("disabled");
    }

    if (status.intelligence_ready) {
        intelligenceButton.href = status.intelligence_url;
        intelligenceButton.classList.remove("disabled");
    } else {
        intelligenceButton.href = "#";
        intelligenceButton.classList.add("disabled");
    }
}

function updateRunStatus(status) {
    state.lastRunStatus = status;

    const progress = Number(status.progress || 0);
    byId("progressPercent").textContent = `${progress}%`;
    byId("progressLabel").textContent = (
        status.stage_label || "Ready"
    );
    byId("progressBar").style.width = `${progress}%`;
    byId("runStartedAt").textContent = (
        status.started_at
            ? `Started ${formatDateTime(status.started_at)}`
            : "Not started"
    );
    byId("runModeLabel").textContent = status.run_ai
        ? "Local scoring + AI intelligence"
        : "Local scoring + zero-cost market statistics";

    const logs = Array.isArray(status.logs)
        ? status.logs
        : [];
    const logElement = byId("runLogs");
    logElement.textContent = (
        logs.length
            ? logs.join("\n")
            : "Waiting for a run…"
    );

    if (status.running) {
        logElement.scrollTop = logElement.scrollHeight;
    }

    byId("stopButton").classList.toggle(
        "hidden",
        !status.running,
    );

    const errorElement = byId("runError");

    if (status.error) {
        errorElement.textContent = status.error;
        errorElement.classList.remove("hidden");
    } else {
        errorElement.classList.add("hidden");
    }

    updateResultLinks(status);

    if (
        !status.running
        && status.stage === "complete"
    ) {
        stopPolling();
        showSection("results");
        showMessage(
            "Search complete. Your reports are ready.",
            "success",
        );
    }

    if (
        !status.running
        && ["error", "cancelled"].includes(status.stage)
    ) {
        stopPolling();
    }
}

async function pollRunStatus() {
    try {
        const status = await api("/api/run/status");
        updateRunStatus(status);
    } catch (error) {
        byId("runError").textContent = error.message;
        byId("runError").classList.remove("hidden");
    }
}

function startPolling() {
    stopPolling();
    pollRunStatus();
    state.pollTimer = window.setInterval(
        pollRunStatus,
        700,
    );
}

function stopPolling() {
    if (state.pollTimer) {
        window.clearInterval(state.pollTimer);
        state.pollTimer = null;
    }
}

async function stopRun() {
    try {
        await api("/api/run/stop", {
            method: "POST",
            body: "{}",
        });
        showMessage(
            "Stopping the current run…",
            "success",
        );
    } catch (error) {
        showMessage(error.message, "error");
    }
}

async function resetRules() {
    const confirmed = window.confirm(
        "Restore the original scoring and location rules? "
        + "Your résumé and company selection will be preserved.",
    );

    if (!confirmed) {
        return;
    }

    try {
        const payload = await api("/api/config/reset", {
            method: "POST",
            body: "{}",
        });

        const currentProfile = deepClone(state.config.profile);
        const currentCompanies = deepClone(
            state.config.companies,
        );
        const currentSources = deepClone(state.config.sources);
        const currentAgent = deepClone(state.config.agent);

        state.config = payload.config;
        state.config.profile = currentProfile;
        state.config.companies = currentCompanies;
        state.config.sources = currentSources;
        state.config.agent = currentAgent;

        populateRules();
        updateRunSummary();
        setDirty();
        showMessage(
            "Default scoring rules restored. Save when ready.",
            "success",
        );
    } catch (error) {
        showMessage(error.message, "error");
    }
}

function bindNavigation() {
    document.querySelectorAll(".nav-item").forEach((button) => {
        button.addEventListener("click", () => {
            showSection(button.dataset.section);
        });
    });

    document.querySelectorAll(".next-button").forEach((button) => {
        button.addEventListener("click", () => {
            gatherConfigFromForm();
            showSection(button.dataset.next);
        });
    });

    document.querySelectorAll(".back-button").forEach((button) => {
        button.addEventListener("click", () => {
            showSection(button.dataset.back);
        });
    });

    document.querySelectorAll(".nav-jump").forEach((button) => {
        button.addEventListener("click", () => {
            showSection(button.dataset.section);
        });
    });
}

function bindInputs() {
    [
        "resumeText",
        "desiredJobText",
        "locationPreference",
        "additionalNotes",
        "excludedJobTerms",
        "excludedExperienceLevels",
        "localAreaTerms",
        "remoteTerms",
        "hybridTerms",
        "onsiteTerms",
        "minPossibleScore",
        "minStrongScore",
        "aiModel",
        "topJobs",
    ].forEach((id) => {
        byId(id).addEventListener("input", () => {
            setDirty();
            updateRunSummary();
        });
    });

    [
        "sourceGreenhouse",
        "sourceAshby",
        "sourceLever",
        "refreshAi",
        "forceDiscovery",
    ].forEach((id) => {
        byId(id).addEventListener("change", () => {
            setDirty();
            updateRunSummary();
        });
    });

    byId("runAi").addEventListener("change", () => {
        updateAiAvailability();
    });

    byId("companySearch").addEventListener(
        "input",
        renderCompanies,
    );
    byId("companyCategory").addEventListener(
        "change",
        renderCompanies,
    );

    byId("selectCoreCompanies").addEventListener(
        "click",
        () => {
            state.selectedCompanies = new Set(
                state.companies
                    .filter((company) => Number(company.priority) <= 1)
                    .map((company) => company.name),
            );
            renderCompanies();
            updateCompanyCount();
            updateRunSummary();
            setDirty();
        },
    );

    byId("selectAllCompanies").addEventListener(
        "click",
        () => {
            state.selectedCompanies = new Set(
                state.companies.map((company) => company.name),
            );
            renderCompanies();
            updateCompanyCount();
            updateRunSummary();
            setDirty();
        },
    );

    byId("clearCompanies").addEventListener(
        "click",
        () => {
            state.selectedCompanies.clear();
            renderCompanies();
            updateCompanyCount();
            updateRunSummary();
            setDirty();
        },
    );

    byId("addCustomCompany").addEventListener(
        "click",
        addCustomCompany,
    );

    byId("customCompanyName").addEventListener(
        "keydown",
        (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                addCustomCompany();
            }
        },
    );

    byId("saveButton").addEventListener(
        "click",
        async () => {
            try {
                await saveConfig(true);
            } catch (error) {
                showMessage(error.message, "error");
            }
        },
    );

    byId("resetRules").addEventListener(
        "click",
        resetRules,
    );
    byId("runButton").addEventListener(
        "click",
        startRun,
    );
    byId("stopButton").addEventListener(
        "click",
        stopRun,
    );
}

async function bootstrap() {
    try {
        const payload = await api("/api/bootstrap");

        state.config = payload.config;
        state.defaultConfig = deepClone(payload.config);
        state.companies = payload.companies || [];
        state.categories = payload.categories || [];
        state.apiKey = payload.api_key || state.apiKey;

        state.config.companies = (
            state.config.companies
            || { enabled: [], custom: [] }
        );
        state.config.companies.custom = (
            state.config.companies.custom || []
        );

        state.selectedCompanies = new Set(
            state.config.companies.enabled || [],
        );

        populateProfile();
        populateCategories();
        renderCompanies();
        renderCustomCompanies();
        populateRules();
        populateSourcesAndAgent();
        updateCompanyCount();
        updateApiKeyStatus();
        updateRunStatus(payload.run || {});
        setDirty(false);

        const requestedSection = location.hash.replace("#", "");

        if (
            requestedSection
            && document.getElementById(requestedSection)
        ) {
            showSection(requestedSection);
        } else if (payload.run?.running) {
            showSection("progress");
        } else {
            showSection("profile");
        }

        if (payload.run?.running) {
            startPolling();
        }
    } catch (error) {
        showMessage(
            `Could not start the application: ${error.message}`,
            "error",
        );
    }
}

window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) {
        return;
    }

    event.preventDefault();
    event.returnValue = "";
});

bindNavigation();
bindInputs();
bootstrap();
