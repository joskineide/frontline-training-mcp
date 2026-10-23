-- Fake demo data for local development. Every company, person and module is fictional.
-- It replaces all tenant data, so the runner only applies it with APP_ENV=dev.
-- Dates are relative to today, so "overdue" stays meaningful whenever this is loaded.

TRUNCATE tenant, team, app_user, module, assignment, announcement, audit_log
    RESTART IDENTITY CASCADE;

INSERT INTO tenant (tenant_id, name) VALUES
    ('acme',   'Acme Retail'),
    ('globex', 'Globex Logistics');

-- Each team names its manager; the managers are inserted just below (that foreign key is checked at commit).
INSERT INTO team (tenant_id, team_id, name, manager_id) VALUES
    ('acme',   'store-12',        'Store 12',        'marcos.reis'),
    ('acme',   'store-7',         'Store 7',         'paula.mendes'),
    ('globex', 'warehouse-north', 'Warehouse North', 'li.wei');

INSERT INTO app_user (tenant_id, user_id, team_id, display_name, role) VALUES
    ('acme',   'marcos.reis',  NULL,              'Marcos Reis',  'manager'),
    ('acme',   'paula.mendes', NULL,              'Paula Mendes', 'manager'),
    ('acme',   'ana.souza',    'store-12',        'Ana Souza',    'worker'),
    ('acme',   'joao.lima',    'store-12',        'João Lima',    'worker'),
    ('acme',   'bea.costa',    'store-12',        'Bea Costa',    'worker'),
    ('acme',   'rui.alves',    'store-7',         'Rui Alves',    'worker'),
    ('globex', 'li.wei',       NULL,              'Li Wei',       'manager'),
    ('globex', 'omar.haddad',  'warehouse-north', 'Omar Haddad',  'worker'),
    ('globex', 'sofia.rossi',  'warehouse-north', 'Sofia Rossi',  'worker');

INSERT INTO module (tenant_id, module_id, title, summary, content, duration_minutes) VALUES
    ('acme', 'forklift-safety', 'Forklift Safety',
     'Pre-shift inspection, load limits and pedestrian zones in the stockroom.',
     'Before each shift, walk around the forklift and check the tires, forks, mast chains, brakes, horn and lights. Never exceed the capacity on the load plate, and travel with forks low. Pedestrians always have the right of way in marked aisles. Report any defect to your manager before operating.',
     25),
    ('acme', 'food-handling-basics', 'Food Handling Basics',
     'Temperature control, cross-contamination and handwashing at the deli counter.',
     'Keep cold food at or below 5 °C and hot food at or above 60 °C; the range between is the danger zone. Use separate boards and utensils for raw and ready-to-eat food. Wash your hands for at least 20 seconds before handling food and after breaks.',
     15),
    ('acme', 'cash-handling', 'Cash Handling',
     'Counting the float, two-person counts and reporting discrepancies.',
     'Count the opening float with a second team member and sign the count sheet together. Never leave a till unlocked. Report any difference above the store limit to the manager on duty before the end of your shift.',
     20),
    ('acme', 'customer-returns', 'Customer Returns',
     'Receipt checks, item condition and refunds to the original payment method.',
     'Ask for the receipt or order number and check the return window. Inspect the item and its packaging. Refunds always go back to the original payment method; offer store credit only when no receipt is available.',
     15),
    ('acme', 'fire-evacuation', 'Fire Evacuation',
     'Alarms, exits and the assembly point.',
     'When the alarm sounds, stop work and guide customers to the nearest marked exit. Never use the elevators. Gather at the assembly point in the parking lot and report to your manager for the headcount.',
     10),
    ('acme', 'slip-and-fall-prevention', 'Slip and Fall Prevention',
     'Spotting, signing and cleaning up spills on the shop floor.',
     'Place a wet-floor sign as soon as you see a spill, then clean it up or call someone who can. Keep aisles free of boxes and cables. Report damaged flooring or poor lighting to your manager.',
     10),
    ('acme', 'store-opening-checklist', 'Store Opening Checklist',
     'Alarm, lights, tills and safety walk before the doors open.',
     'Disarm the alarm, switch on the lights and the tills, and walk the floor for hazards. Check that the emergency exits are clear and unlocked from the inside. Open the doors only when the opening checklist is signed.',
     15),
    ('acme', 'store-closing-checklist', 'Store Closing Checklist',
     'Tills, cold storage, doors and alarm at closing time.',
     'Count and lock the tills with a second team member. Check the cold storage temperatures and log them. Walk the store to make sure no one is left inside, lock the doors and set the alarm.',
     15),
    ('globex', 'forklift-safety', 'Warehouse Forklift Operation',
     'Load centers, stacking heights and dock plates in the warehouse.',
     'Check the load center before lifting: a load further from the mast reduces the safe capacity. Never stack above the marked rack height. Confirm the dock plate is secure and the trailer wheels are chocked before driving onto a trailer.',
     30),
    ('globex', 'hazmat-labels', 'Hazardous Materials Labels',
     'Reading hazard pictograms and keeping incompatible goods apart.',
     'Learn the hazard pictograms for flammable, corrosive and toxic goods. Keep incompatible classes in their assigned zones. Never accept a package with a damaged or missing label; quarantine it and inform your manager.',
     20),
    ('globex', 'loading-dock-safety', 'Loading Dock Safety',
     'Trailer restraints, dock edges and traffic around the docks.',
     'Engage the trailer restraint before anyone enters a trailer. Stay clear of the dock edge when no trailer is present. Use the marked walkways; forklifts and trucks have limited visibility near the docks.',
     20),
    ('globex', 'pallet-jack-basics', 'Pallet Jack Basics',
     'Safe pushing, pulling and parking of manual pallet jacks.',
     'Push rather than pull whenever you can, and keep loads within the rated capacity. Lower the forks fully when parking. Never ride on a pallet jack.',
     10),
    ('globex', 'cold-chain-handling', 'Cold Chain Handling',
     'Keeping temperature-controlled goods between 2 and 8 °C.',
     'Move refrigerated goods into cold storage within 15 minutes of unloading. Record the temperature at receipt and at dispatch. If a reading is out of range, quarantine the goods and inform your manager.',
     20),
    ('globex', 'incident-reporting', 'Incident Reporting',
     'What to report, when, and how.',
     'Report every injury, near miss and damaged load the same day, even if no one was hurt. Describe what happened, where and when, without assigning blame. Reports help prevent the next incident.',
     10),
    ('globex', 'defensive-driving', 'Defensive Driving',
     'Following distance, fatigue and route planning for delivery drivers.',
     'Keep at least a four-second following distance, and more in rain. Take a break every two hours or when you feel tired. Plan the route before departure and avoid using the phone while driving.',
     30);

INSERT INTO assignment (tenant_id, worker_id, module_id, assigned_by, due_date, completed_at) VALUES
    ('acme',   'ana.souza',   'forklift-safety',          'marcos.reis',  current_date + 7,  NULL),
    ('acme',   'ana.souza',   'food-handling-basics',     'marcos.reis',  current_date - 5,  now() - interval '10 days'),
    ('acme',   'ana.souza',   'cash-handling',            'marcos.reis',  current_date - 2,  NULL),  -- overdue
    ('acme',   'ana.souza',   'fire-evacuation',          'marcos.reis',  NULL,              NULL),
    ('acme',   'joao.lima',   'forklift-safety',          'marcos.reis',  current_date - 1,  now() - interval '3 days'),
    ('acme',   'joao.lima',   'customer-returns',         'marcos.reis',  current_date + 14, NULL),
    ('acme',   'bea.costa',   'food-handling-basics',     'marcos.reis',  current_date + 3,  NULL),
    ('acme',   'bea.costa',   'slip-and-fall-prevention', 'marcos.reis',  current_date - 8,  now() - interval '9 days'),
    ('acme',   'rui.alves',   'forklift-safety',          'paula.mendes', current_date + 10, NULL),
    ('acme',   'marcos.reis', 'fire-evacuation',          'paula.mendes', current_date - 20, now() - interval '25 days'),
    ('globex', 'omar.haddad', 'hazmat-labels',            'li.wei',       current_date + 5,  NULL),
    ('globex', 'omar.haddad', 'forklift-safety',          'li.wei',       current_date - 3,  NULL),  -- overdue
    ('globex', 'sofia.rossi', 'cold-chain-handling',      'li.wei',       current_date - 4,  now() - interval '6 days'),
    ('globex', 'sofia.rossi', 'loading-dock-safety',      'li.wei',       current_date + 12, NULL);

INSERT INTO announcement (tenant_id, team_id, author_id, title, body, status, published_at, published_by) VALUES
    ('acme', 'store-12', 'marcos.reis', 'Store opens late on Monday',
     'Because of the inventory count, Store 12 opens at 11:00 on Monday. Morning shift: please arrive at 9:00 to help with counting.',
     'published', now() - interval '1 day', 'marcos.reis'),
    ('acme', 'store-12', 'marcos.reis', 'Holiday schedule',
     'Holiday shifts will be posted next week. Tell me about any constraints by Friday.',
     'draft', NULL, NULL),
    ('acme', 'store-7', 'paula.mendes', 'New returns policy',
     'From the first of the month, returns without a receipt get store credit only. See the Customer Returns module.',
     'published', now() - interval '3 days', 'paula.mendes'),
    ('globex', 'warehouse-north', 'li.wei', 'Dock 3 closed for repairs',
     'Dock 3 is closed until Thursday. Use docks 1 and 2 and expect longer turnaround times.',
     'published', now() - interval '2 hours', 'li.wei');
