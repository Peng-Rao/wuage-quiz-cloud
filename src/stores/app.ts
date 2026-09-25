import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 当前学段与学科 */
export const useAppStore = defineStore('app', () => {
  const stage = ref('高中')
  const subject = ref('数学')

  function pickSubject(s: string, sub: string) {
    stage.value = s
    subject.value = sub
  }

  return { stage, subject, pickSubject }
})
