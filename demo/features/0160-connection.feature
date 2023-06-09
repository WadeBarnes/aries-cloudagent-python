Feature: RFC 0160 Aries agent connection functions

   @T001-RFC0160 @GHA
   Scenario Outline: establish a connection between two agents
      Given we have "2" agents
         | name  | role    | capabilities        |
         | Acme  | inviter | <Acme_capabilities> |
         | Bob   | invitee | <Bob_capabilities>  |
      When "Acme" generates a connection invitation
      And "Bob" receives the connection invitation
      Then "Acme" has an active connection
      And "Bob" has an active connection

      Examples:
         | Acme_capabilities                      | Bob_capabilities          |
         | --public-did --mediation               | --mediation               |
