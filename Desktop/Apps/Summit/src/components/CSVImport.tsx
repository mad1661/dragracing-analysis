'use client'

import { useState } from 'react'
import Papa from 'papaparse'
import Fuse from 'fuse.js'
import { collection, getDocs, addDoc, writeBatch, doc } from 'firebase/firestore'
import { db } from '@/lib/firebase/client'
import { Race } from '@/types/events'
import { User } from '@/types/user'

interface CSVImportProps {
  eventId: string
  divisionId: string
  onImportComplete: (races: Race[]) => void
  onClose: () => void
}

interface CSVRow {
  racerName: string
  racerNumber: string
  class: string
  round: string
  et: string
  reactionTime: string
  speed: string
  dialIn?: string
  won: string
}

export default function CSVImport({
  eventId,
  divisionId,
  onImportComplete,
  onClose,
}: CSVImportProps) {
  const [file, setFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [errors, setErrors] = useState<string[]>([])
  const [preview, setPreview] = useState<CSVRow[]>([])

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0]
    if (selectedFile) {
      setFile(selectedFile)
      setErrors([])
      previewCSV(selectedFile)
    }
  }

  const previewCSV = (file: File) => {
    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      preview: 5,
      complete: (results) => {
        setPreview(results.data as CSVRow[])
      },
      error: (error) => {
        setErrors([`Failed to parse CSV: ${error.message}`])
      },
    })
  }

  const handleImport = async () => {
    if (!file) return

    setLoading(true)
    setErrors([])
    setProgress(0)

    try {
      // Step 1: Fetch all users for fuzzy matching
      const usersSnapshot = await getDocs(collection(db, 'users'))
      const users = usersSnapshot.docs.map((doc) => ({
        uid: doc.id,
        ...doc.data(),
      })) as User[]

      // Create fuse.js instance for fuzzy matching
      const fuse = new Fuse(users, {
        keys: ['displayName', 'email'],
        threshold: 0.3, // Adjust sensitivity (0 = exact match, 1 = match anything)
      })

      // Step 2: Parse CSV
      Papa.parse(file, {
        header: true,
        skipEmptyLines: true,
        complete: async (results) => {
          const rows = results.data as CSVRow[]
          const validRaces: Omit<Race, 'id'>[] = []
          const parseErrors: string[] = []

          // Step 3: Validate and match each row
          rows.forEach((row, index) => {
            try {
              // Validate required fields
              if (
                !row.racerName ||
                !row.racerNumber ||
                !row.class ||
                !row.round ||
                !row.et ||
                !row.reactionTime ||
                !row.speed
              ) {
                parseErrors.push(
                  `Row ${index + 1}: Missing required fields`
                )
                return
              }

              // Parse numbers
              const et = parseFloat(row.et)
              const reactionTime = parseFloat(row.reactionTime)
              const speed = parseFloat(row.speed)
              const dialIn = row.dialIn ? parseFloat(row.dialIn) : undefined

              if (isNaN(et) || isNaN(reactionTime) || isNaN(speed)) {
                parseErrors.push(
                  `Row ${index + 1}: Invalid numeric values (ET, RT, or Speed)`
                )
                return
              }

              // Fuzzy match racer name to existing users
              const fuseResults = fuse.search(row.racerName)
              const racerId = fuseResults.length > 0 ? fuseResults[0].item.uid : ''

              // Parse "won" field (accepts: true, yes, 1, win)
              const won =
                row.won?.toLowerCase() === 'true' ||
                row.won?.toLowerCase() === 'yes' ||
                row.won === '1' ||
                row.won?.toLowerCase() === 'win'

              validRaces.push({
                eventId,
                divisionId,
                class: row.class.trim(),
                round: row.round.trim(),
                racerId,
                racerName: row.racerName.trim(),
                racerNumber: row.racerNumber.trim(),
                et,
                reactionTime,
                speed,
                dialIn,
                won,
                timestamp: new Date(),
              })
            } catch (err) {
              parseErrors.push(
                `Row ${index + 1}: ${err instanceof Error ? err.message : 'Unknown error'}`
              )
            }
          })

          if (parseErrors.length > 0) {
            setErrors(parseErrors)
          }

          if (validRaces.length === 0) {
            setErrors([...parseErrors, 'No valid races to import'])
            setLoading(false)
            return
          }

          // Step 4: Batch write to Firestore
          try {
            const batch = writeBatch(db)
            const racesCollection = collection(db, 'races')

            validRaces.forEach((race) => {
              const raceRef = doc(racesCollection)
              batch.set(raceRef, race)
            })

            await batch.commit()

            // Step 5: Notify parent component
            const racesWithIds = validRaces.map((race, i) => ({
              id: `imported-${i}`,
              ...race,
            })) as Race[]

            onImportComplete(racesWithIds)
            onClose()
          } catch (err) {
            setErrors([
              `Failed to save races: ${err instanceof Error ? err.message : 'Unknown error'}`,
            ])
          } finally {
            setLoading(false)
          }
        },
        error: (error) => {
          setErrors([`Failed to parse CSV: ${error.message}`])
          setLoading(false)
        },
      })
    } catch (err) {
      setErrors([
        `Import failed: ${err instanceof Error ? err.message : 'Unknown error'}`,
      ])
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-800 p-6">
        <h3 className="mb-4 text-2xl font-bold text-white">Import Race Results from CSV</h3>

        <div className="space-y-6">
          {/* File Upload */}
          <div>
            <label className="block text-sm font-medium text-zinc-300">
              Select CSV File
            </label>
            <input
              type="file"
              accept=".csv"
              onChange={handleFileSelect}
              disabled={loading}
              className="mt-2 block w-full text-sm text-zinc-400 file:mr-4 file:rounded-lg file:border-0 file:bg-red-600 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white hover:file:bg-red-700 disabled:opacity-50"
            />
            <p className="mt-2 text-xs text-zinc-500">
              CSV must include columns: racerName, racerNumber, class, round, et,
              reactionTime, speed, won (optional: dialIn)
            </p>
          </div>

          {/* Preview */}
          {preview.length > 0 && !loading && (
            <div>
              <h4 className="mb-2 text-sm font-medium text-zinc-300">
                Preview (first 5 rows)
              </h4>
              <div className="overflow-x-auto rounded-lg border border-zinc-700 bg-zinc-900">
                <table className="w-full text-sm">
                  <thead className="border-b border-zinc-700 bg-zinc-800">
                    <tr>
                      <th className="px-4 py-2 text-left text-zinc-400">Racer</th>
                      <th className="px-4 py-2 text-left text-zinc-400">Number</th>
                      <th className="px-4 py-2 text-left text-zinc-400">Class</th>
                      <th className="px-4 py-2 text-left text-zinc-400">ET</th>
                      <th className="px-4 py-2 text-left text-zinc-400">RT</th>
                      <th className="px-4 py-2 text-left text-zinc-400">Won</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.map((row, i) => (
                      <tr key={i} className="border-b border-zinc-700/50">
                        <td className="px-4 py-2 text-white">{row.racerName}</td>
                        <td className="px-4 py-2 text-zinc-400">{row.racerNumber}</td>
                        <td className="px-4 py-2 text-zinc-400">{row.class}</td>
                        <td className="px-4 py-2 text-zinc-400">{row.et}</td>
                        <td className="px-4 py-2 text-zinc-400">{row.reactionTime}</td>
                        <td className="px-4 py-2 text-zinc-400">{row.won}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Errors */}
          {errors.length > 0 && (
            <div className="rounded-lg bg-red-500/10 border border-red-500/50 p-4">
              <h4 className="mb-2 text-sm font-semibold text-red-400">Errors:</h4>
              <ul className="list-disc space-y-1 pl-5 text-sm text-red-400">
                {errors.map((error, i) => (
                  <li key={i}>{error}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Progress */}
          {loading && (
            <div className="rounded-lg bg-zinc-900 p-4">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="text-zinc-300">Importing...</span>
                <span className="text-zinc-400">{progress}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-zinc-700">
                <div
                  className="h-full bg-red-600 transition-all duration-300"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="flex-1 rounded-lg border border-zinc-600 px-4 py-2 text-white hover:bg-zinc-700 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleImport}
              disabled={!file || loading}
              className="flex-1 rounded-lg bg-red-600 px-4 py-2 font-semibold text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'Importing...' : 'Import Races'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
