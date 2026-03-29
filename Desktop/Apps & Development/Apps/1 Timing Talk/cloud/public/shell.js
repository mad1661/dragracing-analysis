(function initTimingTalkShell() {
  if (!document.body || document.body.dataset.shellBuilt === "1") return;
  document.body.dataset.shellBuilt = "1";

  const PRIMARY_ITEMS = [
    { id: "event-set", label: "Event Set", href: "/event-set.html", icon: "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" },
    { id: "results", label: "Results", href: "/results.html", icon: "M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2" },
    { id: "search", label: "Search / Timeslips", href: "/search.html", icon: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" },
    { id: "schedule", label: "Schedule", href: "/schedule.html", icon: "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" },
    { id: "schedule-builder", label: "Schedule Builder", href: "/schedule-builder.html", icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" },
    { id: "noshows", label: "No Shows", href: "/noshows.html", icon: "M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" },
    { id: "best-package", label: "Best Package", href: "/best-package.html", icon: "M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" },
    { id: "dead-on", label: "Dead On", href: "/dead-on.html", icon: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" },
    { id: "perfect-rt", label: "Perfect RT", href: "/perfect-rt.html", icon: "M13 10V3L4 14h7v7l9-11h-7z" },
    { id: "racer", label: "Racer Profile", href: "/racer.html", icon: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" },
    { id: "stats", label: "Statistics", href: "/stats.html", icon: "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" },
    { id: "brackets", label: "Brackets", href: "/brackets.html", icon: "M4 6h16M4 12h8m-8 6h16" },
    { id: "announcer", label: "Announcer", href: "/announcer.html", icon: "M15.536 8.464a5 5 0 010 7.072M18.364 5.636a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" },
  ];

  const UTILITY_ITEMS = [
    { id: "settings", label: "Device Settings", href: "/settings.html" },
    { id: "class-setup", label: "Class Setup", href: "/class-setup.html", icon: "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z" },
    { id: "feed", label: "Raw Data Feed", href: "/feed.html" },
    { id: "tech-cards", label: "Tech Cards", href: "/tech-cards.html" },
    { id: "racer-notes", label: "Racer Notes", href: "/racer-notes.html", icon: "M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" },
    { id: "admin", label: "Admin", href: "/admin.html" },
  ];

  function getPrimaryActiveId() {
    if (location.pathname.startsWith("/raw/")) {
      const view = (new URLSearchParams(location.search).get("view") || "runs").toLowerCase();
      if (["runs", "schedule", "timeslip", "noshows"].includes(view)) return view;
    }
    return document.body.dataset.navPrimary || "";
  }

  function getUtilityActiveId() {
    return document.body.dataset.navUtility || "";
  }

  function renderNavItems(items, activeId) {
    return items.map((item) => {
      const isActive = item.id === activeId;
      return `
        <a href="${item.href}" class="tt-shell-link${isActive ? " active" : ""}">
          ${item.icon ? `
            <svg class="tt-shell-link-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="${item.icon}"></path>
            </svg>
          ` : `<span class="tt-shell-link-dot"></span>`}
          <span>${item.label}</span>
        </a>
      `;
    }).join("");
  }

  function buildSidebar() {
    const primaryActiveId = getPrimaryActiveId();
    const utilityActiveId = getUtilityActiveId();

    const sidebar = document.createElement("aside");
    sidebar.className = "tt-shell-sidebar";
    sidebar.id = "ttShellSidebar";
    sidebar.innerHTML = `
      <div class="tt-shell-brand">
        <a href="/apps.html" class="tt-shell-brand-link">
          <div class="tt-shell-brand-mark">TT</div>
          <div>
            <div class="tt-shell-brand-title">Timing Talk</div>
            <div class="tt-shell-brand-subtitle">Hosted Timing Suite</div>
          </div>
        </a>
      </div>

      <div class="tt-shell-nav-section">
        <div class="tt-shell-section-label">Race Data</div>
        ${renderNavItems(PRIMARY_ITEMS, primaryActiveId)}
      </div>

      <div class="tt-shell-nav-section">
        <div class="tt-shell-section-label">Utilities</div>
        ${renderNavItems(UTILITY_ITEMS, utilityActiveId)}
      </div>

      <div class="tt-shell-footer">
        <div class="tt-shell-auth">
          <span class="nav-user hidden" id="nav-user"></span>
          <a href="/dashboard.html" class="tt-shell-auth-link" id="nav-login">Sign In</a>
          <button class="tt-shell-auth-link hidden" id="nav-logout" type="button" onclick="handleShellSignOut()">Sign Out</button>
        </div>
      </div>
    `;
    return sidebar;
  }

  function wrapExistingContent() {
    const children = Array.from(document.body.children).filter((node) => node.tagName !== "SCRIPT");
    const main = document.createElement("div");
    main.className = "tt-shell-main";

    const content = document.createElement("div");
    content.className = "tt-shell-content";
    children.forEach((child) => content.appendChild(child));

    main.appendChild(content);
    return main;
  }

  function installShell() {
    const firstScript = document.body.querySelector("script");
    const toggle = document.createElement("button");
    toggle.className = "tt-shell-toggle";
    toggle.id = "ttShellToggle";
    toggle.type = "button";
    toggle.setAttribute("aria-label", "Toggle navigation");
    toggle.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 6h16M4 12h16M4 18h16"></path>
      </svg>
    `;

    const overlay = document.createElement("div");
    overlay.className = "tt-shell-overlay";
    overlay.id = "ttShellOverlay";

    const sidebar = buildSidebar();
    const main = wrapExistingContent();

    document.body.classList.add("tt-shell-ready");
    document.body.insertBefore(toggle, firstScript);
    document.body.insertBefore(overlay, firstScript);
    document.body.insertBefore(sidebar, firstScript);
    document.body.insertBefore(main, firstScript);

    function closeSidebar() {
      sidebar.classList.remove("is-open");
      overlay.classList.remove("is-open");
    }

    function openSidebar() {
      sidebar.classList.add("is-open");
      overlay.classList.add("is-open");
    }

    toggle.addEventListener("click", () => {
      if (sidebar.classList.contains("is-open")) closeSidebar();
      else openSidebar();
    });
    overlay.addEventListener("click", closeSidebar);
    sidebar.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeSidebar));
  }

  async function refreshLiveDirectoryCard() {
    const meta = document.getElementById("ttShellLiveMeta");
    const note = document.getElementById("ttShellLiveNote");
    const dot = document.getElementById("ttShellLiveDot");
    if (!meta || !note || !dot) return;

    try {
      const response = await fetch("/api/tracks?online=true");
      if (!response.ok) throw new Error("API offline");
      const data = await response.json();
      const tracks = data.tracks || [];
      meta.textContent = tracks.length ? `${tracks.length} online` : "0 online";
      note.textContent = tracks.length
        ? `${tracks[0].name || tracks[0].trackName || tracks[0].promoter || "Track"} live now`
        : "No tracks currently online";
      dot.classList.toggle("is-live", tracks.length > 0);
    } catch (error) {
      meta.textContent = "Unavailable";
      note.textContent = "Public track directory not reachable";
      dot.classList.remove("is-live");
    }
  }

  window.handleShellSignOut = async function handleShellSignOut() {
    if (typeof doSignOut === "function") {
      return doSignOut();
    }
    if (typeof handleSignOut === "function") {
      return handleSignOut();
    }
    if (typeof signOut === "function") {
      await signOut();
      location.reload();
    }
  };

  installShell();
  refreshLiveDirectoryCard();
})();
