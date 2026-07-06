@api
Feature: Chain of Custody content validation

  Scenario: CoC export round-trips for a file with events
    Given an evidence file "sample.mp4" with a view and a download event
    When I export the chain of custody as "sergeant1" in "csv"
    Then the CoC export succeeds
