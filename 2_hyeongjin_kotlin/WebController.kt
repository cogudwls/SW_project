// WebController.kt - 시연 담당: 코틀린 웹 서버가 브라우저에 주는 주소
//   GET  /admin        관리자 화면   (src/main/resources/static/admin.html)
//   GET  /m            관객 화면     (src/main/resources/static/mobile.html)
//   GET  /api/status   화면용 상태 (FastAPI에서 TCP로 받은 최신 값)
//   POST /api/control  관리자 제어 → TCP로 FastAPI에 전달 (ack 확인)
//
// ※ 맨 위 package 줄은 형진이 프로젝트 패키지 이름으로 바꾸기
package com.team.crowd

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import org.springframework.http.ResponseEntity
import org.springframework.stereotype.Controller
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController

@Controller
class PageController {
    @GetMapping("/admin")
    fun admin() = "forward:/admin.html"

    @GetMapping("/m")
    fun mobile() = "forward:/mobile.html"
}

@RestController
class StatusController(
    private val link: FastApiLink,
    private val mapper: ObjectMapper,
) {
    @GetMapping("/api/status")
    fun status(): ResponseEntity<JsonNode> {
        val s = link.latestStatus.get()
            ?: return ResponseEntity.status(503).body(detail("FastAPI 서버 연결 대기 중이에요"))
        val copy = s.deepCopy<JsonNode>() as ObjectNode
        copy.set<JsonNode>("web_link", mapper.valueToTree(link.stats()))   // 코틀린 쪽 통신 상태도 같이
        return ResponseEntity.ok(copy)
    }

    @PostMapping("/api/control")
    fun control(@RequestBody body: Map<String, Any?>): ResponseEntity<JsonNode> {
        val action = body["action"] as? String
            ?: return ResponseEntity.badRequest().body(detail("action이 없어요"))
        if (action == "message") {
            val text = (body["text"] as? String ?: "").trim().uppercase()
            if (text.isEmpty() || text.length > 20 || text.any { it.code < 32 || it.code > 126 }) {
                return ResponseEntity.badRequest().body(detail("LCD 문구는 영문·숫자 20자 이내로 입력해 주세요"))
            }
        }
        val sent = link.send(mapOf<String, Any?>("type" to "control") + body, reliable = true)
        if (!sent) return ResponseEntity.status(503).body(detail("FastAPI 서버와 연결되어 있지 않아요"))
        Thread.sleep(300)                       // FastAPI가 처리하고 새 상태를 보낼 시간
        return status()
    }

    private fun detail(msg: String): JsonNode = mapper.createObjectNode().put("detail", msg)
}
