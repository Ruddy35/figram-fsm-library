import Figram.Run
import Figram.Lint
import Figram.FormatProofs
import Figram.FormatIO
import Figram.Trace
import Figram.RandomDoc

/-!
# Пример: бот пиццерии

Демонстрирует все ярлыки:

* раздел «bot» вокруг всего — глобальная команда `/start` с любого экрана;
* раздел «order» — кнопка «Отмена» один раз на весь заказ;
* `/help` из любого места и «Назад» туда, где был;
* напоминание на корзине;
* условия текстом: «корзина не пуста», «адрес в зоне доставки».
-/

namespace Figram.Examples

open Figram Figram.Format

private def v (to : NodeId) (calls : List String := []) : Variant := ⟨none, to, false, calls, []⟩
private def vIf (c : String) (to : NodeId) (calls : List String := []) : Variant :=
  ⟨some c, to, false, calls, []⟩
private def back (s : NodeId) : Variant := ⟨none, s, true, [], []⟩
private def msg (on : List (EventId × List Variant)) : NodeDoc := .message on [] []

/-- Бот пиццерии в формате документа. -/
def pizzaDoc : Doc where
  requires := []
  conditions := [
    ("cart_not_empty", "В корзине есть хотя бы одна пицца"),
    ("in_delivery_zone", "Адрес входит в зону доставки")]
  calls := [
    ("clear_cart", "Очистить корзину"),
    ("save_size", "Запомнить выбранный размер"),
    ("add_to_cart", "Добавить пиццу в корзину"),
    ("save_address", "Сохранить адрес из сообщения"),
    ("create_order", "Создать заказ в CRM")]
  variables := [
    ("cart", "Содержимое корзины"),
    ("address", "Адрес доставки")]
  bot := .sec "menu" [
      ("menu", msg [("btn:order", [v "order"])]),
      ("help", msg [("btn:back", [back "order"])]),
      ("done", msg []),
      ("reminder", msg [("btn:back", [back "order"])]),
      ("order", .sec "catalog" [
          ("catalog", msg [("btn:pizza", [v "pizza"]), ("btn:cart", [v "cart"])]),
          ("pizza", .sec "pick_size" [
              ("pick_size", msg [("btn:size", [v "pick_toppings" ["save_size"]])]),
              ("pick_toppings", msg [("btn:add", [v "cart" ["add_to_cart"]])])
            ] [] []),
          ("cart", .message [
              ("btn:checkout", [vIf "cart_not_empty" "address", v "catalog"]),
              ("timer:cart_reminder", [v "reminder"])
            ] [("cart_reminder", 2 * 60 * 60 * 1000)] []),
          ("address", msg [
              ("msg:text", [vIf "in_delivery_zone" "pay" ["save_address"], v "pickup"])]),
          ("pay", msg [("payment:success", [v "done" ["create_order"]])]),
          ("pickup", msg [])
        ] [("btn:cancel", [v "menu" ["clear_cart"]])] [])
    ] [("cmd:start", [v "menu"]), ("cmd:help", [v "help"])] []
  ext := []

/-- Схема, которую исполняет автомат. -/
def pizza : Chart := pizzaDoc.toChart

/-- Документ со всеми особыми случаями поведения. -/
def featuresDoc : Doc where
  requires := []
  conditions := [
    ("is_vip", "Пользователь — VIP"),
    ("local_ok", "Локальная проверка в каталоге"),
    ("in_stock", "Товар есть на складе")]
  calls := [("charge", "Списать оплату"), ("notify", "Уведомить менеджера")]
  variables := []
  bot := .sec "home" [
      ("home", .message [
          ("btn:go", [vIf "is_vip" "vip", v "shop"]),
          ("btn:self", [v "home"])
        ] [("idle", 60000)] []),
      ("help", msg [("btn:back", [back "shop"])]),
      ("vip", msg []),
      ("paid", msg []),
      ("shop", .sec "catalog" [
          ("catalog", msg [
              ("btn:next", [v "sub"]),
              ("btn:x", [vIf "local_ok" "item2"])]),
          ("sub", .sec "item" [
              ("item", .message [
                  ("btn:buy", [vIf "in_stock" "paid" ["charge", "notify"], v "catalog"]),
                  ("btn:other", [v "item2"]),
                  ("timer:remind", [v "reminder"])
                ] [("remind", 5000)] []),
              ("item2", msg [("btn:back_item", [v "item"])])
            ] [] []),
          ("reminder", msg [("btn:back", [back "shop"])])
        ] [("btn:cancel", [v "home"]), ("btn:x", [v "reminder"])] [])
    ] [("cmd:start", [v "home"]), ("cmd:help", [v "help"])] []
  ext := []

def features : Chart := featuresDoc.toChart

#guard featuresDoc.check.isEmpty

open Trace in
/-- Фиксированные сценарии: каждый проверяет конкретное поведение. -/
def golden : List (String × Doc × Option Snapshot × List In) := [
  ("pizza_order_success", pizzaDoc, none, [
    .ev "btn:order", .ev "btn:pizza", .ev "btn:size", .ev "btn:add",
    .ev "btn:checkout" [("cart_not_empty", true)],
    .ev "msg:text" [("in_delivery_zone", true)],
    .ev "payment:success"]),
  ("pizza_empty_cart_and_pickup", pizzaDoc, none, [
    .ev "btn:order", .ev "btn:cart",
    .ev "btn:checkout" [("cart_not_empty", false)],
    .ev "btn:cart",
    .ev "btn:checkout" [("cart_not_empty", true)],
    .ev "msg:text" [("in_delivery_zone", false)]]),
  ("guard_true", featuresDoc, none, [.ev "btn:go" [("is_vip", true)]]),
  ("guard_false", featuresDoc, none, [.ev "btn:go" [("is_vip", false)]]),
  ("bubble_after_false_local", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:x" [("local_ok", false)]]),
  ("local_true_no_bubble", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:x" [("local_ok", true)]]),
  ("nested_resume", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next", .ev "btn:other",
    .ev "cmd:help", .ev "btn:back"]),
  ("stale_resume_value", featuresDoc,
    some ⟨"help", [("shop", "deleted_message")], [], 7⟩, [.ev "btn:back"]),
  ("self_loop_restarts_timer", featuresDoc, none, [.ev "btn:self", .ev "btn:self"]),
  ("timer_tokens", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next",
    .ev "btn:other", .ev "btn:back_item",
    .timerStale "remind" 4, .timerNow "remind"]),
  ("buy_action_order", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next",
    .ev "btn:buy" [("in_stock", true)]]),
  ("recover_deleted_current", featuresDoc,
    some ⟨"deleted_message", [("shop", "item")], [("old_timer", 3)], 9⟩,
    [.ev "btn:go" [("is_vip", true)]])
]

def runGolden (name : String) : Option (Snapshot × List (List Action)) :=
  (golden.find? (·.1 == name)).map fun (_, d, f, ins) =>
    let (_, s, acts) := Trace.scripted d.toChart name f ins
    (s, acts)

def finalAt (name : String) : String := ((runGolden name).map (·.1.state)).getD "?"
def allActs (name : String) : List Action := ((runGolden name).map (·.2.flatten)).getD []

-- успешный заказ доходит до конца и вызывает create_order
#guard finalAt "pizza_order_success" == "done"
#guard (allActs "pizza_order_success").contains (.call "create_order")
-- обе стороны условий
#guard finalAt "guard_true" == "vip"
#guard finalAt "guard_false" == "catalog"
-- ложная локальная ветка уходит к разделу
#guard finalAt "bubble_after_false_local" == "reminder"
#guard finalAt "local_true_no_bubble" == "item2"
-- «вернуться туда, где был» во вложенный раздел
#guard finalAt "nested_resume" == "item2"
-- устаревшее запомненное место — начальное сообщение раздела
#guard finalAt "stale_resume_value" == "catalog"
-- устаревший жетон игнорируется, текущий — срабатывает
#guard finalAt "timer_tokens" == "reminder"
-- удалённое текущее сообщение: начало бота и отмена старых таймеров
#guard finalAt "recover_deleted_current" == "home"
#guard (allActs "recover_deleted_current").take 1 == [.cancelTimer "old_timer"]
-- порядок действий: выход → действия стрелки → вход
#guard ((runGolden "buy_action_order").map (·.2.getLast!)) ==
  some [.cancelTimer "remind", .call "charge", .call "notify", .send "paid"]
-- петля на себя перезапускает таймер с новым жетоном
#guard ((runGolden "self_loop_restarts_timer").map (·.2.getLast!)) ==
  some [.cancelTimer "idle", .startTimer "idle" 60000 3, .send "home"]

-- все документы-примеры и случайные документы проходят текстовый круг
def textRoundTrip (d : Doc) : Bool :=
  match parse (print (encode d)) >>= decode with
  | .ok d' => print (encode d') == print (encode d)
  | .error _ => false

#guard textRoundTrip pizzaDoc && textRoundTrip featuresDoc
#guard (List.range 40).all fun i => let d := RandomDoc.doc i 10; d.check.isEmpty && textRoundTrip d

-- дубликат ключа ловится проверкой до экспорта (F05)
#guard !({ pizzaDoc with conditions := [("same", "one"), ("same", "two")] }).check.isEmpty

-- пустые имена отвергаются (F02)
#guard match parse "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"on\": {\"\": [{\"to\": \"\"}]}}}}}" >>= decode with
  | .ok d => !d.check.isEmpty
  | .error _ => true

-- огромный показатель степени отвергается сразу (F03)
#guard match parse "{\"x\": 1e1000000000}" with
  | .ok _ => false
  | .error _ => true

-- неизвестный ключ читается и сохраняется, поведение не меняется
#guard match parse "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"x-ui\": {\"x\": 10}}}}, \"x-author\": \"Анна\"}" >>= decode with
  | .ok d => d.ext.length == 1 && d.check.isEmpty
  | .error _ => false

-- документ с незнакомой возможностью отвергается целиком
#guard match parse "{\"figram\": 1, \"requires\": [\"parallel\"], \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {}}}}" >>= decode with
  | .ok _ => false
  | .error _ => true

-- повторяющийся ключ отвергается парсером
#guard match parse "{\"a\": 1, \"a\": 2}" with
  | .ok _ => false
  | .error _ => true

#guard pizza.compile.wf && features.compile.wf

end Figram.Examples
