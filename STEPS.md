# Step Catalogue

Use these steps to write new scenarios. No TypeScript or Python needed.

## Auth
| Step | Layer | Roles |
|---|---|---|
| `Given I am logged in as "{role}"` | @ui @api | officer1, officer2, sergeant1, admin, iauser, sysops1 |
| `And I switch to user "{role}"` | @api | Re-authenticates context mid-scenario; roles same as Given |

## Records
| Step | Layer | Notes |
|---|---|---|
| `When I create a new evidence record titled "{title}"` | @ui @api | Prefix title with `[E2E] ` |
| `When I navigate to my records` | @ui | |
| `When I try to navigate to officer1's record` | @ui | Tests cross-user access |
| `Then I should see my records listed` | @ui | |

## Files
| Step | Layer | Files available |
|---|---|---|
| `And I upload the file "{filename}"` | @ui @api | sample.mp4, sample.pdf, sample.jpg |
| `And I try to upload the file "{filename}"` | @api | unsupported.svg |
| `Then the file should appear in the record's file list` | @ui @api | |
| `And an audit event "{event}" should exist for the file` | @api | e.g. UPLOAD_COMPLETED |
| `And I have a record with an uploaded file` | @api | Creates [E2E] record + uploads sample.pdf |
| `When I try to upload a file to officer1's record` | @api | Tests cross-user upload (403 expected) |

## File Lock
| Step | Layer | Levels |
|---|---|---|
| `When I set the file lock to "{level}"` | @api | private, invisible |
| `Then the file should not be visible to "{role}"` | @api | |
| `But the file should be visible to "{role}"` | @api | |
| `Then I should be able to see the file` | @api | |

## Sharing
| Step | Layer | Notes |
|---|---|---|
| `When I create an external share link for the file` | @ui @api | |
| `Then the share link should be accessible without login` | @ui @api | |
| `And the recipient can download the file with a download reason` | @ui @api | |
| `And an external share link has expired` | @api | Creates and force-expires a share link |
| `When I visit the share link` | @api | |
| `Then I should see an "expired" message` | @api | |

## Admin
| Step | Layer | Notes |
|---|---|---|
| `When I navigate to user management` | @ui @api | |
| `When I try to navigate to user management` | @ui @api | Tests unauthorized access |
| `Then I should see all users belonging to my agency` | @ui @api | |
| `When I set the retention period for my agency to {n} days` | @api | |
| `Then the retention policy should be saved` | @api | |
| `When I try to set a retention policy` | @api | |

## Audit & Chain of Custody
| Step | Layer | Notes |
|---|---|---|
| `Given an evidence file "{filename}" with a view and a download event` | @api | Minimal CoC setup |
| `Given officer1 performs the full allowed lifecycle on "{filename}"` | @api | create→upload→view→play→download→privatize→share→revoke→unprivatize, each verified + logged |
| `When I export the chain of custody as "{role}" in "{fmt}"` | @api | fmt = csv or pdf; role needs audit decrypt (sergeant1/admin) |
| `Then the CoC export succeeds` | @api | 200 + correct content-type |
| `Then the CoC CSV contains every allowed lifecycle event with correct category and outcome` | @api | |
| `Then the CoC events are in timestamp order` | @api | |
| `Then the CoC PDF text contains each allowed action verb` | @api | pdfplumber text extraction |
| `When denied actors attempt to access the file` | @api | iauser/sysops1/officer2 + standard-user CoC export |
| `Then each denied attempt returns 403` | @api | officer2 non-403 tolerated (env alias) |
| `Then denied audit rows are characterized against the file CoC` | @api | prints a finding; see design §8 |

## Errors
| Step | Layer |
|---|---|
| `Then I should receive a 403 error` | @api |
| `Then I should be redirected or see a 403 error` | @ui |
| `Then I should see an error about unsupported file type` | @api |
