'use client'

import { useEffect, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import {
  doc,
  getDoc,
  collection,
  query,
  where,
  orderBy,
  getDocs,
  addDoc,
} from 'firebase/firestore'
import { db } from '@/lib/firebase/client'
import { Event, Race } from '@/types/events'

export default function EventDetailPage() {
  const router = useRouter()
  const params = useParams()
  const eventId = params.id as string
  const { user, loading: authLoading, signOut } = useAuth()
  const [event, setEvent] = useState<Event | null>(null)
  const [races, setRaces] = useState<Race[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedClass, setSelectedClass] = useState<string>('all')
  const [showAddRaceModal, setShowAddRaceModal] = useState(false)
  const [newRace, setNewRace] = useState({
    racerName: '',
    racerNumber: '',
    class: '',
    round: '',
    et: '',
    reactionTime: '',
    speed: '',
    dialIn: '',
    won: false,
  })

  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/login')
    }
  }, [user, authLoading, router])

  useEffect(() => {
    const fetchEventAndRaces = async () => {
      if (!eventId) return

      try {
        // Fetch event
        const eventDoc = await getDoc(doc(db, 'events', eventId))
        if (eventDoc.exists()) {
          const eventData = {
            id: eventDoc.id,
            ...eventDoc.data(),
            date: eventDoc.data().date.toDate(),
            createdAt: eventDoc.data().createdAt.toDate(),
          } as Event
          setEvent(eventData)

          // Fetch races for this event
          const racesQuery = query(
            collection(db, 'races'),
            where('eventId', '==', eventId),
            orderBy('timestamp', 'desc')
          )
          const racesSnapshot = await getDocs(racesQuery)
          const racesData = racesSnapshot.docs.map((doc) => ({
            id: doc.id,
            ...doc.data(),
            timestamp: doc.data().timestamp.toDate(),
          })) as Race[]
          setRaces(racesData)
        }
      } catch (error) {
        console.error('Error fetching event:', error)
      } finally {
        setLoading(false)
      }
    }

    if (user) {
      fetchEventAndRaces()
    }
  }, [eventId, user])

  const handleAddRace = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!user || !event) return

    try {
      const raceData: Omit<Race, 'id'> = {
        eventId,
        divisionId: event.divisionId,
        class: newRace.class,
        round: newRace.round,
        racerId: '', // Will be matched later via fuzzy search
        racerName: newRace.racerName,
        racerNumber: newRace.racerNumber,
        et: parseFloat(newRace.et),
        reactionTime: parseFloat(newRace.reactionTime),
        speed: parseFloat(newRace.speed),
        dialIn: newRace.dialIn ? parseFloat(newRace.dialIn) : undefined,
        won: newRace.won,
        timestamp: new Date(),
      }

      const docRef = await addDoc(collection(db, 'races'), raceData)
      setRaces([{ id: docRef.id, ...raceData }, ...races])
      setShowAddRaceModal(false)
      setNewRace({
        racerName: '',
        racerNumber: '',
        class: '',
        round: '',
        et: '',
        reactionTime: '',
        speed: '',
        dialIn: '',
        won: false,
      })
    } catch (error) {
      console.error('Error adding race:', error)
      alert('Failed to add race result')
    }
  }

  const handleSignOut = async () => {
    await signOut()
    router.push('/')
  }

  const canAddRaces = user?.role === 'super_admin' || user?.role === 'division_director'

  const filteredRaces =
    selectedClass === 'all'
      ? races
      : races.filter((r) => r.class === selectedClass)

  if (authLoading || loading || !user || !event) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-zinc-900 via-zinc-800 to-zinc-900">
        <div className="text-white">Loading...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-zinc-900 via-zinc-800 to-zinc-900">
      {/* Navigation */}
      <nav className="border-b border-zinc-700 bg-zinc-800/50 backdrop-blur">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex h-16 items-center justify-between">
            <div className="flex items-center gap-8">
              <h1 className="text-xl font-bold text-white">Summit Racing</h1>
              <div className="hidden space-x-4 md:flex">
                <a href="/dashboard" className="text-zinc-400 hover:text-white">
                  Dashboard
                </a>
                <a href="/events" className="text-white hover:text-red-400">
                  Events
                </a>
                {user.role === 'super_admin' && (
                  <a href="/admin" className="text-zinc-400 hover:text-white">
                    Admin
                  </a>
                )}
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <div className="text-sm font-medium text-white">{user.displayName}</div>
                <div className="text-xs text-zinc-400">{user.role}</div>
              </div>
              <button
                onClick={handleSignOut}
                className="rounded-lg bg-zinc-700 px-4 py-2 text-sm text-white hover:bg-zinc-600"
              >
                Sign Out
              </button>
            </div>
          </div>
        </div>
      </nav>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* Event Header */}
        <div className="mb-8">
          <button
            onClick={() => router.push('/events')}
            className="mb-4 text-zinc-400 hover:text-white"
          >
            ← Back to Events
          </button>

          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-3xl font-bold text-white">{event.name}</h2>
              <div className="mt-2 flex gap-4 text-zinc-400">
                <span>📅 {event.date.toLocaleDateString()}</span>
                <span>📍 {event.location}</span>
                <span className="capitalize">
                  {event.status.replace('_', ' ')}
                </span>
              </div>
            </div>
            {canAddRaces && (
              <button
                onClick={() => setShowAddRaceModal(true)}
                className="rounded-lg bg-red-600 px-6 py-3 font-semibold text-white transition-colors hover:bg-red-700"
              >
                Add Race Result
              </button>
            )}
          </div>
        </div>

        {/* Class Filter */}
        <div className="mb-6 flex gap-2">
          <button
            onClick={() => setSelectedClass('all')}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              selectedClass === 'all'
                ? 'bg-red-600 text-white'
                : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'
            }`}
          >
            All Classes
          </button>
          {event.classes.map((cls) => (
            <button
              key={cls}
              onClick={() => setSelectedClass(cls)}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                selectedClass === cls
                  ? 'bg-red-600 text-white'
                  : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'
              }`}
            >
              {cls}
            </button>
          ))}
        </div>

        {/* Race Results */}
        <div className="rounded-lg border border-zinc-700 bg-zinc-800/50 backdrop-blur">
          <div className="border-b border-zinc-700 p-6">
            <h3 className="text-xl font-bold text-white">Race Results</h3>
            <p className="mt-1 text-sm text-zinc-400">
              {filteredRaces.length} results
            </p>
          </div>

          {filteredRaces.length === 0 ? (
            <div className="p-12 text-center text-zinc-400">
              No race results yet for this event
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="border-b border-zinc-700 bg-zinc-800/50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-zinc-400">
                      Racer
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-zinc-400">
                      Class
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-zinc-400">
                      Round
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-zinc-400">
                      ET
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-zinc-400">
                      RT
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-zinc-400">
                      Speed
                    </th>
                    <th className="px-6 py-3 text-center text-xs font-medium uppercase tracking-wider text-zinc-400">
                      Result
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-700">
                  {filteredRaces.map((race) => (
                    <tr key={race.id} className="hover:bg-zinc-700/30">
                      <td className="whitespace-nowrap px-6 py-4">
                        <div className="text-sm font-medium text-white">
                          {race.racerName}
                        </div>
                        <div className="text-xs text-zinc-400">
                          #{race.racerNumber}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-6 py-4 text-sm text-zinc-400">
                        {race.class}
                      </td>
                      <td className="whitespace-nowrap px-6 py-4 text-sm text-zinc-400">
                        {race.round}
                      </td>
                      <td className="whitespace-nowrap px-6 py-4 text-right text-sm font-medium text-white">
                        {race.et.toFixed(3)}
                      </td>
                      <td className="whitespace-nowrap px-6 py-4 text-right text-sm text-zinc-400">
                        {race.reactionTime.toFixed(3)}
                      </td>
                      <td className="whitespace-nowrap px-6 py-4 text-right text-sm text-zinc-400">
                        {race.speed.toFixed(2)} mph
                      </td>
                      <td className="whitespace-nowrap px-6 py-4 text-center">
                        {race.won ? (
                          <span className="inline-flex rounded-full bg-green-500/20 px-2 py-1 text-xs font-semibold text-green-400">
                            Win
                          </span>
                        ) : (
                          <span className="inline-flex rounded-full bg-zinc-600 px-2 py-1 text-xs font-semibold text-zinc-300">
                            Loss
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>

      {/* Add Race Modal */}
      {showAddRaceModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-800 p-6">
            <h3 className="mb-4 text-2xl font-bold text-white">Add Race Result</h3>

            <form onSubmit={handleAddRace} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-zinc-300">
                    Racer Name
                  </label>
                  <input
                    type="text"
                    required
                    value={newRace.racerName}
                    onChange={(e) =>
                      setNewRace({ ...newRace, racerName: e.target.value })
                    }
                    className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 px-4 py-2 text-white focus:border-red-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-zinc-300">
                    Racer Number
                  </label>
                  <input
                    type="text"
                    required
                    value={newRace.racerNumber}
                    onChange={(e) =>
                      setNewRace({ ...newRace, racerNumber: e.target.value })
                    }
                    className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 px-4 py-2 text-white focus:border-red-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-zinc-300">
                    Class
                  </label>
                  <select
                    required
                    value={newRace.class}
                    onChange={(e) => setNewRace({ ...newRace, class: e.target.value })}
                    className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 px-4 py-2 text-white focus:border-red-500 focus:outline-none"
                  >
                    <option value="">Select class</option>
                    {event.classes.map((cls) => (
                      <option key={cls} value={cls}>
                        {cls}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-medium text-zinc-300">
                    Round
                  </label>
                  <input
                    type="text"
                    required
                    value={newRace.round}
                    onChange={(e) => setNewRace({ ...newRace, round: e.target.value })}
                    className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 px-4 py-2 text-white focus:border-red-500 focus:outline-none"
                    placeholder="Round 1, Semifinals, Final"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-zinc-300">
                    ET (seconds)
                  </label>
                  <input
                    type="number"
                    step="0.001"
                    required
                    value={newRace.et}
                    onChange={(e) => setNewRace({ ...newRace, et: e.target.value })}
                    className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 px-4 py-2 text-white focus:border-red-500 focus:outline-none"
                    placeholder="10.452"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-zinc-300">
                    Reaction Time (seconds)
                  </label>
                  <input
                    type="number"
                    step="0.001"
                    required
                    value={newRace.reactionTime}
                    onChange={(e) =>
                      setNewRace({ ...newRace, reactionTime: e.target.value })
                    }
                    className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 px-4 py-2 text-white focus:border-red-500 focus:outline-none"
                    placeholder="0.523"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-zinc-300">
                    Speed (mph)
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    value={newRace.speed}
                    onChange={(e) => setNewRace({ ...newRace, speed: e.target.value })}
                    className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 px-4 py-2 text-white focus:border-red-500 focus:outline-none"
                    placeholder="125.43"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-zinc-300">
                    Dial-In (optional)
                  </label>
                  <input
                    type="number"
                    step="0.001"
                    value={newRace.dialIn}
                    onChange={(e) => setNewRace({ ...newRace, dialIn: e.target.value })}
                    className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 px-4 py-2 text-white focus:border-red-500 focus:outline-none"
                    placeholder="10.500"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="won"
                  checked={newRace.won}
                  onChange={(e) => setNewRace({ ...newRace, won: e.target.checked })}
                  className="h-4 w-4 rounded border-zinc-600 bg-zinc-900 text-red-600 focus:ring-red-500"
                />
                <label htmlFor="won" className="text-sm font-medium text-zinc-300">
                  Won this race
                </label>
              </div>

              <div className="flex gap-3 pt-4">
                <button
                  type="button"
                  onClick={() => setShowAddRaceModal(false)}
                  className="flex-1 rounded-lg border border-zinc-600 px-4 py-2 text-white hover:bg-zinc-700"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="flex-1 rounded-lg bg-red-600 px-4 py-2 font-semibold text-white hover:bg-red-700"
                >
                  Add Result
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
