# Positioning — Why Agent School

Agent School exists to make one argument concrete: an enterprise AI platform is not an agent execution place, it is an agentic platform. The difference is the whole value, and it is easy to miss until you try to run a real agent against a real domain.

## Consumer agents and enterprise agents are not the same thing

A consumer agent runs on a laptop or a Mac mini with the person's own keys, their own data, and the skills they personally grant it. If it goes wrong, the blast radius is one person, and that person pays the price. This is a legitimate and useful shape, and it is what most "run an agent in five minutes" material demonstrates.

An enterprise agent is a different animal. Picture a squid: its arms reach into surrounding data sources, and its tentacles attach to platform capabilities. The agent has no value in isolation. It is only useful when the platform underneath it is already up and running with the dependencies and capabilities of its domain (telco, healthcare, transportation, and so on) in place. The blast radius is the organization, not the individual, so identity, governance, isolation, and auditability are not optional extras. They are the reason the platform exists.

## Why this is not a "quickstart"

Because the enterprise agent depends on the platform being present, you cannot honestly package it as a five-minute quickstart. There is no "quicky" version of an agent that needs a feature store, a served model, a governed tool gateway, and a workload identity before it can do anything real. The courses can run their loop mechanics offline on a laptop for learning, but their point is what happens when they land on a platform that carries the domain. Any catalog or starter-kit framing has to keep that caveat honest: the courses can seed a catalog, but the platform comes first.

## What the platform actually delivers

An enterprise agentic platform is what lets a swarm of agents work together safely across boundaries. Two boundaries matter:

**East-West (intra-working).** Agents cooperating with each other inside a domain: one diagnoses, one plans, one acts, one validates, over an agent-to-agent protocol, each with its own identity, each reachable only through governed paths. This is the closed loop 301 demonstrates.

**North-South (inter-working).** Agents reaching out of their loop to platform capabilities and to the outside: a model served as a stateless endpoint, a classic ML scorer reached only through a governed gateway, a feature store that guarantees train-serve parity, a tool call authorized by token claims at the boundary rather than trusted inside the agent.

Each domain gets these boundaries drawn for it. That is what turns a pile of agents into a governed platform, and it is exactly what a single agent execution runtime does not give you.

## How the courses demonstrate it

Each course isolates one part of the enterprise argument so it can be seen and trusted: 101 shows the governed agent loop and the model as a served endpoint; 201 shows evidence-grounded reasoning measured as platform data; 202 shows a classic model consumed as a governed tool with a human-approval gate; 301 closes the loop with four agents acting under a governed actuation path; 302 has a calibrated model and a GenAI judge co-decide, then measures the judge. Read together, they are not five demos. They are one platform argument, told in five governed workloads.

## The catalog question

Getting the courses in front of people through AI Quickstarts, starter-kits, or a platform agent catalog is a distribution win, and worth doing. The framing to hold is yes-and: the courses can be the first entries in such a catalog, and every entry carries the same honest line that the platform and its domain dependencies must be up first. A catalog of enterprise agents is a catalog of platform workloads, not a catalog of quickstarts.
