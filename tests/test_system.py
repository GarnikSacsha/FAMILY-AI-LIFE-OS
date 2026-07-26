import unittest

from app.orchestration.router import IntentRouter


class TestFamilyLifeOS(unittest.TestCase):
    def test_intent_router_food_vision(self):
        res = IntentRouter.classify_intent("Вот мой сегодняшний обед", has_photo=True)
        self.assertEqual(res["intent"], "FOOD_NUTRITION_ANALYSIS")
        self.assertEqual(res["primary_agent"], "health")

    def test_intent_router_health_query(self):
        res = IntentRouter.classify_intent("Как я спал сегодня ночью по Oura?")
        self.assertEqual(res["intent"], "HEALTH_BIOMETRICS_QUERY")
        self.assertEqual(res["primary_agent"], "health")

    def test_intent_router_financial_query(self):
        res = IntentRouter.classify_intent("Сколько мы потратили на рестораны?")
        self.assertEqual(res["intent"], "FINANCIAL_QUERY_OR_LOG")
        self.assertEqual(res["primary_agent"], "finance")

    def test_intent_router_planner(self):
        res = IntentRouter.classify_intent("Добавь молоко в список покупок")
        self.assertEqual(res["intent"], "PLANNING_OR_REMINDER")
        self.assertEqual(res["primary_agent"], "planner")


if __name__ == "__main__":
    unittest.main()
