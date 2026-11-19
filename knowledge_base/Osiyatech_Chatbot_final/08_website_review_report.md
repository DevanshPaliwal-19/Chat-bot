# Website Review Report

**Site:** https://ayushitated.com (Osiyatech staging)  
**Review date:** 2026-11-02

## How the site was reviewed
- The 7 pages in the supplied archive (stg.zip) were parsed: Home, About, Services, Industries, Case Studies, Hire Experts, Start a Project.
- The live staging site was opened in a browser. Inner pages return HTTP 404 when opened directly, but render through in-site navigation; the 13 service pages and the Custom Software page were read this way, including FAQ answers hidden in collapsed accordions, all offering tabs, technology tabs and project pop-up data.
- Footer links were checked. Privacy Policy, Terms of Service and Security & Compliance point to '#contact' and have no pages.
- External profiles (Clutch, LinkedIn, Instagram) and client websites were not reviewed.

## Pages successfully reviewed (20)
- Home - https://ayushitated.com/
- About Us - https://ayushitated.com/about
- Services (catalog) - https://ayushitated.com/services
- Industries - https://ayushitated.com/industries
- Case Studies & Portfolio - https://ayushitated.com/case-studies
- Hire Experts - https://ayushitated.com/hire-experts
- Start a Project (Contact + FAQ) - https://ayushitated.com/start-a-project
- Web Applications - https://ayushitated.com/web-development
- Mobile Applications - https://ayushitated.com/mobile-development
- UI/UX Design - https://ayushitated.com/ui-ux-design
- QA & Automated Testing - https://ayushitated.com/qa-testing
- AI & Machine Learning - https://ayushitated.com/ai-machine-learning
- AI Agents & Automation - https://ayushitated.com/ai-agents-automation
- BI & Data Analytics - https://ayushitated.com/data-analytics
- Enterprise Software Development - https://ayushitated.com/services/enterprise-software-development/
- ERP & CRM Solutions - https://ayushitated.com/services/erp-crm-solutions/
- Enterprise System Integration - https://ayushitated.com/services/enterprise-system-integration/
- Cloud & DevOps - https://ayushitated.com/cloud-devops
- Cybersecurity - https://ayushitated.com/cybersecurity
- Zero-Downtime Migration - https://ayushitated.com/cloud-migration

## Pages partially reviewed
- Custom Software & Core Business Systems - https://ayushitated.com/custom-software-development: Live staging site. Not listed in the Services catalog; headline statistics are marketing claims.

## Inaccessible / no content
- Privacy Policy (Inaccessible): Footer link exists but does not lead to a policy page.
- Terms of Service (Inaccessible): Footer link exists but does not lead to a terms page.
- Security & Compliance (Inaccessible): Footer link exists but does not lead to a page.
- Our Process / Work Models / Tech Expertise / Contact Us (footer) (No relevant content found): No dedicated pages exist for these footer items.
- Newsletter 'Stay Updated' (No relevant content found): No newsletter policy or frequency documented.
- Clutch / LinkedIn / Instagram / WhatsApp (No relevant content found): External profiles are outside the staging website scope.

## Output counts
- Services documented: 14 (13 catalog services + Custom Software page)
- Portfolio projects: 36
- FAQs: 137 (53 general + 84 service-specific)
- Policy records: 17 (8 not documented)
- RAG knowledge units: 223
- Conflicts identified: 11

## Key findings
- No privacy policy, terms of service, pricing, payment, refund or business-hours information is published.
- Several company figures conflict across pages (founding year, projects shipped, 95% metric label).
- Some service pages attach detailed outcome metrics to projects (Innessco, Jewelers' Assist, My People) that conflict with the Case Studies descriptions; these are flagged and excluded from approved answers.
- Deep links to inner pages return 404 on the staging server - worth fixing before launch, since the chatbot will cite these URLs.
