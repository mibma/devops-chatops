from src.engine.command_parser import CommandParser
from src.models.intents import Action


def test_structured_deploy():
    parser = CommandParser()
    intent = parser.parse("service=api env=production version=2.1.4", action_hint=Action.DEPLOY)
    assert intent.action == Action.DEPLOY
    assert intent.service == "api"
    assert intent.environment == "production"
    assert intent.version == "2.1.4"


def test_freeform_rollback():
    parser = CommandParser()
    intent = parser.parse("please rollback the payment service in staging")
    assert intent.action == Action.ROLLBACK
    assert intent.environment == "staging"
    assert intent.service == "payment"


def test_env_alias_normalization():
    parser = CommandParser()
    intent = parser.parse("service=api env=prod", action_hint=Action.STATUS)
    assert intent.environment == "production"
