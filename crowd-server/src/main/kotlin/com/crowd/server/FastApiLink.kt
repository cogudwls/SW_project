package com.crowd.server

import jakarta.annotation.PostConstruct
import jakarta.annotation.PreDestroy
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Component
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentLinkedDeque
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

@Component
class FastApiLink(
    private val mapper: ObjectMapper,
    @Value("\${fastapi.host:172.20.10.6}")
    private val host: String,
    @Value("\${fastapi.link-port:5002}")
    private val port: Int
) {
    val latestStatus = AtomicReference<JsonNode?>(null)

    @Volatile
    final var connected = false
        private set

    @Volatile
    private var running = true

    @Volatile
    private var socket: Socket? = null

    @Volatile
    private var lastRx = 0L

    @Volatile
    private var lastHeartbeat = 0L

    @Volatile
    final var retries = 0
        private set

    private data class Pending(
        val json: String,
        var sentAt: Long,
        var tries: Int
    )

    private val outbox = LinkedBlockingQueue<String>()
    private val pending = ConcurrentHashMap<Int, Pending>()
    private val seen = ArrayDeque<Int>()
    private val nextId = AtomicInteger(1)
    private val rttMs = ConcurrentLinkedDeque<Long>()
    private val workers = mutableListOf<Thread>()

    @PostConstruct
    fun start() {
        workers += thread(isDaemon = true, name = "link-recv") {
            connectLoop()
        }

        workers += thread(isDaemon = true, name = "link-send") {
            sendLoop()
        }

        workers += thread(isDaemon = true, name = "link-watch") {
            watchLoop()
        }
    }

    // FastAPI로 보낼 메시지 등록
    @Synchronized
    fun send(
        message: Map<String, Any?>,
        reliable: Boolean = false
    ): Boolean {
        if (!connected) return false

        if (reliable) {
            val id = nextId.getAndIncrement()
            val json = mapper.writeValueAsString(message + ("id" to id))

            pending[id] = Pending(json, now(), 0)
            outbox.offer(json)
        } else {
            outbox.offer(mapper.writeValueAsString(message))
        }

        return true
    }

    // 큐에 등록된 메시지 전송
    private fun sendLoop() {
        while (running) {
            try {
                val json = outbox.poll(500, TimeUnit.MILLISECONDS)
                    ?: continue

                synchronized(this) {
                    val current = socket

                    if (connected && current != null) {
                        val output = current.getOutputStream()
                        output.write(
                            (json + "\n").toByteArray(StandardCharsets.UTF_8)
                        )
                        output.flush()
                    }
                }
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                return
            } catch (e: Exception) {
                closeConnection("메시지 전송 실패: ${e.message}")
            }
        }
    }

    // FastAPI TCP 서버 접속 및 수신
    private fun connectLoop() {
        while (running) {
            try {
                Socket().use { current ->
                    current.connect(InetSocketAddress(host, port), 3000)

                    synchronized(this) {
                        check(running) { "서버 종료 중" }

                        socket = current
                        lastRx = now()
                        lastHeartbeat = 0L
                        connected = true
                    }

                    println("[FastApiLink] $host:$port 접속 성공")

                    send(mapOf("type" to "hello", "role" to "web"))

                    val reader = BufferedReader(
                        InputStreamReader(
                            current.getInputStream(),
                            StandardCharsets.UTF_8
                        )
                    )

                    while (running && !current.isClosed) {
                        val line = reader.readLine() ?: break
                        lastRx = now()

                        if (line.isBlank()) continue

                        val message = try {
                            mapper.readTree(line)
                        } catch (e: Exception) {
                            println("[FastApiLink] JSON 형식 오류")
                            continue
                        }

                        handle(message)
                    }
                }
            } catch (e: Exception) {
                if (running) {
                    println("[FastApiLink] 접속·수신 실패: ${e.message}")
                }
            } finally {
                closeConnection("연결 종료")
            }

            if (!running) return

            println("[FastApiLink] 3초 뒤 재접속")

            try {
                Thread.sleep(3000)
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                return
            }
        }
    }

    // 수신 메시지 처리
    private fun handle(message: JsonNode) {
        when (message.path("type").asText()) {
            "ack" -> {
                val id = message.path("id").asInt()

                pending.remove(id)?.let {
                    rttMs.addLast(now() - it.sentAt)

                    while (rttMs.size > 50) {
                        rttMs.pollFirst()
                    }
                }
            }

            "heartbeat" -> {
                send(mapOf("type" to "heartbeat_ack"))
            }

            "heartbeat_ack" -> Unit

            else -> {
                if (message.has("id")) {
                    val id = message.path("id").asInt()

                    send(mapOf("type" to "ack", "id" to id))

                    synchronized(seen) {
                        if (seen.contains(id)) return

                        seen.addLast(id)

                        if (seen.size > 500) {
                            seen.removeFirst()
                        }
                    }
                }

                if (message.path("type").asText() == "status") {
                    val data = message.path("data")

                    if (data.isObject) {
                        latestStatus.set(data)
                    }
                }
            }
        }
    }

    // 재전송 및 연결 상태 감시
    private fun watchLoop() {
        while (running) {
            try {
                Thread.sleep(200)

                synchronized(this) {
                    if (connected) {
                        val time = now()

                        for ((id, item) in pending) {
                            if (time - item.sentAt >= 2000) {
                                if (item.tries >= 3) {
                                    pending.remove(id)
                                    println(
                                        "[FastApiLink] id=$id 응답 확인 실패"
                                    )
                                } else {
                                    item.sentAt = time
                                    item.tries++
                                    retries++
                                    outbox.offer(item.json)

                                    println(
                                        "[FastApiLink] id=$id 재전송 ${item.tries}회"
                                    )
                                }
                            }
                        }

                        if (time - lastHeartbeat >= 5000) {
                            lastHeartbeat = time
                            send(mapOf("type" to "heartbeat"))
                        }

                        if (time - lastRx >= 15000) {
                            closeConnection("15초 동안 응답 없음")
                        }
                    }
                }
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                return
            } catch (e: Exception) {
                println("[FastApiLink] 감시 오류: ${e.message}")
            }
        }
    }

    @Synchronized
    private fun closeConnection(reason: String) {
        val previous = socket

        connected = false
        socket = null
        latestStatus.set(null)
        pending.clear()
        outbox.clear()

        synchronized(seen) {
            seen.clear()
        }

        runCatching { previous?.close() }

        if (previous != null) {
            println("[FastApiLink] $reason")
        }
    }

    fun stats(): Map<String, Any?> {
        val samples = rttMs.toList()

        return mapOf(
            "connected" to connected,
            "last_rx_ago_s" to
                    if (lastRx == 0L) null else (now() - lastRx) / 1000.0,
            "rtt_last_ms" to samples.lastOrNull(),
            "rtt_avg_ms" to
                    if (samples.isEmpty()) null else samples.average().toLong(),
            "retries" to retries,
            "pending" to pending.size
        )
    }

    @PreDestroy
    fun stop() {
        running = false
        closeConnection("프로그램 종료")
        workers.forEach { it.interrupt() }
    }

    private fun now(): Long = System.currentTimeMillis()
}