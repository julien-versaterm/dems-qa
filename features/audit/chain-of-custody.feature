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
