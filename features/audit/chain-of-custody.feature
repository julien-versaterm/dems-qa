@api
Feature: Chain of Custody content validation

  Scenario: CoC export round-trips for a file with events
    Given an evidence file "sample.mp4" with a view and a download event
    When I export the chain of custody as "sergeant1" in "csv"
    Then the CoC export succeeds

  Scenario: CoC captures the full allowed evidence lifecycle
    Given officer1 performs the full allowed lifecycle on "sample.mp4"
    When I export the chain of custody as "sergeant1" in "csv"
    Then the CoC CSV contains every allowed lifecycle event with correct category and outcome
    And the CoC events are in timestamp order

  Scenario: CoC PDF text contains the allowed action verbs
    Given officer1 performs the full allowed lifecycle on "sample.mp4"
    When I export the chain of custody as "sergeant1" in "pdf"
    Then the CoC PDF text contains each allowed action verb

  Scenario: Denied attempts are rejected and their logging is characterized
    Given officer1 performs the full allowed lifecycle on "sample.mp4"
    When denied actors attempt to access the file
    Then each denied attempt returns 403
    And denied audit rows are characterized against the file CoC
