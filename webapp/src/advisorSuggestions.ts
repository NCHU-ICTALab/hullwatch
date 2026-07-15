export interface AdvisorSuggestion {
  id: string
  label: string
  question: string
}

export function advisorSuggestions(shipId: string): AdvisorSuggestion[] {
  const ship = shipId || '目前選定船舶'
  return [
    { id: 'fleet-priority', label: '清洗優先序', question: '目前全船隊哪些船需要優先處置？請比較 Speed Loss 與每日超額成本。' },
    { id: 'ship-kpi', label: `${ship} 現況`, question: `${ship} 現在的 Speed Loss、狀態與每日超額成本是多少？` },
    { id: 'ship-action', label: '維護建議', question: `${ship} 建議做船殼清洗還是螺旋槳拋光？依據是什麼？` },
    { id: 'status-policy', label: '狀態門檻', question: '密切留意與立即處置的 Speed Loss 標準是什麼？' },
    { id: 'maintenance-actions', label: '維護動作', question: 'PP、UWC、UWI 與 DD 有什麼差別？哪些會改善效能？' },
    { id: 'speed-loss', label: 'Speed Loss', question: 'Speed Loss 是怎麼計算的？符合 ISO 19030 嗎？' },
    { id: 'cost-carbon', label: '成本與碳排', question: '每月超額成本與超額碳排怎麼計算？單位是什麼？' },
    { id: 'fuel-market', label: '油價更新', question: '市場行情多久更新一次？決策情境價會改寫行情嗎？' },
    { id: 'models', label: '模型用途', question: '目前有哪些模型？油耗預測與 Speed Loss 模型分別用在哪裡？' },
    { id: 'noon-report', label: '正午日報', question: '正午日報需要哪些欄位？STW、SOG 與風級怎麼解讀？' },
  ]
}
